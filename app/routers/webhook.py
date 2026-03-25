"""
POST /webhook/retell

Handles real-time Retell AI webhook events with four upgrade tracks applied:

  Track 1 (Tonality):   system_prompt is wrapped by prompt_builder before LLM call.
  Track 3 (Latency):    fused single Supabase join query; streaming + sentence detection.
  Track 4 (State flow): silence counter (Redis), 2-prompt reminder, graceful hang-up,
                         topic-lock drift guard.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.database import get_supabase
from app.models import RetellWebhookRequest
from app.services import openrouter
from app.services.conversation_state import ConversationState
from app.services.prompt_builder import build_prompt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["Webhook"])

# ---------------------------------------------------------------------------
# Sentence-boundary detection regex (Track 3)
# Matches . ! ? followed by a space or end-of-string
# ---------------------------------------------------------------------------
_SENTENCE_END = re.compile(r"[.?!](?:\s|$)")

# ---------------------------------------------------------------------------
# Topic-lock drift phrases (Track 4)
# If any drift phrase appears in the LLM response, replace with safe fallback.
# ---------------------------------------------------------------------------
_DRIFT_PHRASES = [
    "as an ai language model",
    "i was trained by",
    "my knowledge cutoff",
    "as chatgpt",
    "i'm openai",
    "i am openai",
    "as an artificial intelligence",
]

_DRIFT_FALLBACK = (
    "I'm here to assist with your specific needs — "
    "is there something I can help you with today?"
)


def _is_on_topic(text: str) -> bool:
    """Return False if the response leaks AI-identity or training meta-info."""
    lower = text.lower()
    return not any(phrase in lower for phrase in _DRIFT_PHRASES)


# ---------------------------------------------------------------------------
# Agent config lookup — customer-first query
# ---------------------------------------------------------------------------
async def _fetch_agent_config(agent_id: str) -> dict[str, Any]:
    """
    Look up the agent config for a given Retell agent_id.

    Two separate queries instead of a PostgREST embedded join:
      1. customers WHERE retell_agent_id = agent_id  → get customer_id
      2. agent_configs WHERE customer_id = <id>       → get config

    Why two queries instead of a join:
      The single-query approach (.select("id, agent_configs(*)")) requires
      PostgREST to auto-discover the FK relationship. If the FK constraint
      is missing or ambiguous, PostgREST silently returns agent_configs=[]
      and every call_analyzed event logs with empty config.

      Two explicit .eq() queries have no FK dependency and are trivially
      debuggable — each query either returns data or it doesn't.

    Returns a dict with keys: customer_id, reseller_id, agent_config (dict).
    If no agent_configs row exists for the customer, agent_config defaults to {}
    so the call can still be logged without crashing.
    """
    db = get_supabase()

    # ── Step 1: find customer by retell_agent_id ─────────────────────────
    customer_result = (
        db.table("customers")
        .select("id, reseller_id")
        .eq("retell_agent_id", agent_id)
        .single()
        .execute()
    )

    if not customer_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No customer found for retell_agent_id={agent_id}",
        )

    customer = customer_result.data
    customer_id: str = customer["id"]

    # ── Step 2: fetch agent_config (separate query, no FK inference) ──────
    config_result = (
        db.table("agent_configs")
        .select("*")
        .eq("customer_id", customer_id)
        .limit(1)
        .execute()
    )

    configs_list: list[dict[str, Any]] = config_result.data or []
    agent_config: dict[str, Any] = configs_list[0] if configs_list else {}

    if not agent_config:
        logger.warning(
            "No agent_configs row for customer_id=%s (agent_id=%s) — "
            "using defaults. Call will be logged but may lack prosody/model settings.",
            customer_id, agent_id,
        )

    return {
        "customer_id": customer_id,
        "reseller_id": customer["reseller_id"],
        "agent_config": agent_config,
    }


# ---------------------------------------------------------------------------
# Transcript extraction helper
# ---------------------------------------------------------------------------
def _extract_transcript(call: dict[str, Any]) -> list[dict[str, str]]:
    """Convert Retell transcript array to OpenAI-style message list."""
    transcript = call.get("transcript", [])
    messages: list[dict[str, str]] = []
    for turn in transcript:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "agent":
            role = "assistant"
        messages.append({"role": role, "content": content})
    return messages


def _has_user_content(messages: list[dict[str, str]]) -> bool:
    """Return True if the latest turn has non-empty user content."""
    for msg in reversed(messages):
        if msg["role"] == "user":
            return bool(msg["content"].strip())
    return False


# ---------------------------------------------------------------------------
# Call logger
# ---------------------------------------------------------------------------
async def _log_call_to_supabase(
    call: dict[str, Any],
    customer_id: str,
    prosody_style: str = "warm-conversational",
) -> None:
    """Insert / upsert a completed call record into the calls table."""
    import json as _json

    db = get_supabase()

    # Retell sends transcript as a list of {role, content} dicts.
    # Serialize to a JSON string so it fits a TEXT column without data loss.
    raw_transcript = call.get("transcript", [])
    if isinstance(raw_transcript, list):
        transcript = _json.dumps(raw_transcript)
    else:
        transcript = str(raw_transcript)

    duration = call.get("duration_ms")
    duration_seconds = (duration // 1000) if duration else None
    call_id = call.get("call_id")
    caller_number = call.get("from_number")

    outcome = "completed"
    disc_reason = call.get("disconnection_reason", "")
    if disc_reason:
        disc_lower = disc_reason.lower()
        if "transfer" in disc_lower:
            outcome = "transferred"
        elif "voicemail" in disc_lower:
            outcome = "voicemail"
        elif "drop" in disc_lower:
            outcome = "dropped"
        elif "no_answer" in disc_lower or "timeout" in disc_lower:
            outcome = "no_answer"

    started_at_ms = call.get("start_timestamp")
    ended_at_ms = call.get("end_timestamp")
    started_at = (
        datetime.fromtimestamp(started_at_ms / 1000.0, tz=timezone.utc).isoformat()
        if started_at_ms else None
    )
    ended_at = (
        datetime.fromtimestamp(ended_at_ms / 1000.0, tz=timezone.utc).isoformat()
        if ended_at_ms else None
    )

    # ── Raw payload inspection — logged on EVERY call_analyzed ─────────────
    # Check Railway logs for these lines to verify what Retell actually sends.
    call_cost = call.get("call_cost") or {}
    e2e       = call.get("e2e_latency") or {}
    logger.info(
        "RETELL PAYLOAD [%s]  call_cost=%r  e2e_latency=%r  "
        "top_combined_cost=%r  duration_ms=%r",
        call_id,
        dict(call_cost) if isinstance(call_cost, dict) else call_cost,
        dict(e2e)       if isinstance(e2e, dict)       else e2e,
        call.get("combined_cost"),
        call.get("duration_ms"),
    )

    # ── Cost ─────────────────────────────────────────────────────────────────
    # Retell sends combined_cost in USD CENTS (integer), not USD.
    # e.g. a $0.60 call → combined_cost = 60 → divide by 100 → $0.60.
    # The field can appear at the top level OR inside call_cost depending on
    # the Retell API version — check both, always divide by 100.
    cost_usd: float | None = None
    raw_cost = call.get("combined_cost")
    if raw_cost is None and isinstance(call_cost, dict):
        raw_cost = call_cost.get("combined_cost")
    if raw_cost is not None:
        cost_usd = float(raw_cost) / 100.0      # cents → USD

    # ── Latency ──────────────────────────────────────────────────────────────
    # e2e_latency is a dict with percentile keys in MILLISECONDS.
    # p50 = median response latency. Stored as int ms, displayed as ms on frontend.
    latency_p50_ms: int | None = None
    if isinstance(e2e, dict) and e2e:
        raw_p50 = e2e.get("p50") or e2e.get("p_50") or e2e.get("median")
        if raw_p50 is not None:
            latency_p50_ms = int(raw_p50)   # ensure int, already in ms

    # ── Tokens ───────────────────────────────────────────────────────────────
    tokens_used: int | None = None
    if isinstance(call_cost, dict):
        raw_tokens = (
            call_cost.get("total_tokens")
            or call_cost.get("llm_tokens_used")
        )
        if raw_tokens is None:
            inp = call_cost.get("total_input_tokens") or 0
            out = call_cost.get("total_output_tokens") or 0
            raw_tokens = (inp + out) or None
        if raw_tokens is not None:
            tokens_used = int(raw_tokens)

    logger.info(
        "ANALYTICS EXTRACTED [%s]  cost_usd=%s  latency_p50_ms=%s  tokens=%s",
        call_id, cost_usd, latency_p50_ms, tokens_used,
    )

    record = {
        "customer_id": customer_id,
        "retell_call_id": call_id,
        "caller_number": caller_number,
        "duration_seconds": duration_seconds,
        "outcome": outcome,
        "transcript": transcript,
        "started_at": started_at,
        "ended_at": ended_at,
        "cost_usd": cost_usd,
        "latency_p50_ms": latency_p50_ms,
        "tokens_used": tokens_used,
        "prosody_style_used": prosody_style,
    }

    # Explicit check → insert/update instead of upsert(on_conflict=...).
    # upsert requires a UNIQUE constraint on retell_call_id to exist in Supabase.
    # Without it Postgres raises 42P10 and nothing is saved. This pattern works
    # regardless of whether the constraint is declared.
    existing = (
        db.table("calls")
        .select("id")
        .eq("retell_call_id", call_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        db.table("calls").update(record).eq("retell_call_id", call_id).execute()
        logger.info("Updated call %s for customer %s (cost=$%.4f)", call_id, customer_id, cost_usd or 0)
    else:
        db.table("calls").insert(record).execute()
        logger.info("Inserted call %s for customer %s (cost=$%.4f)", call_id, customer_id, cost_usd or 0)


# ---------------------------------------------------------------------------
# Sentence detection + first-sentence flush (Track 3)
# ---------------------------------------------------------------------------
async def _stream_to_first_sentence(
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float,
    http_client: Any,
) -> str:
    """
    Stream tokens from OpenRouter and return the full response text,
    but structured so the first complete sentence is isolated at the front.

    This means Retell can begin TTS synthesis on the first sentence
    (~300–600 ms) while the rest of the response is still being generated.

    The assembled string is returned as a single response — Retell handles
    chunked TTS internally once it receives the full text. The latency win
    comes from the LLM stream starting earlier (no buffer-and-wait).
    """
    buffer = ""
    first_sentence: str | None = None
    remainder_parts: list[str] = []

    async for token in openrouter.chat_completion_stream(
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
        http_client=http_client,
    ):
        buffer += token

        if first_sentence is None:
            # Look for the first sentence boundary
            match = _SENTENCE_END.search(buffer)
            if match:
                first_sentence = buffer[: match.end()].strip()
                # Capture anything after the boundary immediately
                tail = buffer[match.end():]
                if tail:
                    remainder_parts.append(tail)
        else:
            remainder_parts.append(token)

    # Assemble: first_sentence leads (lowest latency to TTS)
    # then remainder follows in the same payload.
    if first_sentence is None:
        # Short answer with no sentence-ending punctuation — return as-is
        return buffer.strip()

    remainder = "".join(remainder_parts).strip()
    if remainder:
        return f"{first_sentence} {remainder}"
    return first_sentence


# ---------------------------------------------------------------------------
# Main webhook handler
# ---------------------------------------------------------------------------
@router.post("/retell")
async def retell_webhook(
    payload: RetellWebhookRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Main Retell webhook handler.

    Retell sends POST requests throughout the call lifecycle.
    For LLM-mode calls: receives conversation state, returns next utterance.
    """
    event = payload.event
    call = payload.call
    agent_id: str = call.get("agent_id", "")
    call_id: str = call.get("call_id", "")

    logger.info("Received Retell event=%s call_id=%s", event, call_id)

    # Shared resources from app.state (set during lifespan)
    http_client = getattr(request.app.state, "http_client", None)
    redis_client = getattr(request.app.state, "redis", None)
    conv_state = ConversationState(redis_client) if redis_client else None

    # ------------------------------------------------------------------
    # call_started — acknowledge
    # ------------------------------------------------------------------
    if event == "call_started":
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # call_ended — Redis cleanup only; wait for call_analyzed for full data
    # ------------------------------------------------------------------
    if event == "call_ended":
        if conv_state and call_id:
            try:
                await conv_state.clear(call_id)
            except Exception as exc:
                logger.error("Redis clear failed for call %s: %s", call_id, exc)
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # call_analyzed — full transcript + cost + latency available, log now
    # ------------------------------------------------------------------
    if event == "call_analyzed":
        try:
            agent_data = await _fetch_agent_config(agent_id)
            prosody_style = agent_data["agent_config"].get("prosody_style", "warm-conversational")
            await _log_call_to_supabase(call, agent_data["customer_id"], prosody_style)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to log call: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to log call transcript",
            ) from exc
        return {"status": "logged"}

    # ------------------------------------------------------------------
    # LLM response event — main conversational turn
    # ------------------------------------------------------------------
    try:
        agent_data = await _fetch_agent_config(agent_id)
        config: dict[str, Any] = agent_data.get("agent_config", {})

        raw_prompt: str = config.get("system_prompt", "")
        prosody_style: str = config.get("prosody_style", "warm-conversational")
        model: str = config.get("llm_model", "openai/gpt-4o-mini")
        temperature: float = float(config.get("temperature", 0.7))
        silence_timeout: int = int(config.get("silence_timeout_seconds", 10))
        max_prompts: int = int(config.get("max_silence_prompts", 2))

        messages = _extract_transcript(call)

        # ── Track 1: wrap prompt with prosody + guardrails ───────────
        system_prompt = build_prompt(raw_prompt, style=prosody_style)

        # ── Track 4: silence detection ───────────────────────────────
        # Retell signals silence via reminder callbacks — no user content
        # in the transcript means this is a silence turn.
        is_silence_turn = not _has_user_content(messages)

        if is_silence_turn and conv_state and call_id:
            silence_count = await conv_state.increment_silence(call_id)

            if silence_count == 1:
                return {
                    "response": (
                        "Are you still there? Take your time — "
                        "I'm right here whenever you're ready."
                    )
                }
            elif silence_count == 2:
                return {
                    "response": (
                        "Just checking in one more time — are you still with me?"
                    )
                }
            else:
                # silence_count >= 3: graceful hang-up
                return {
                    "response": (
                        "It seems like this might not be the best time. "
                        "Feel free to call back whenever you're ready — "
                        "have a great day!"
                    ),
                    "end_call": True,
                }

        # ── Track 4: user spoke — reset silence counter ──────────────
        if not is_silence_turn and conv_state and call_id:
            try:
                await conv_state.record_user_turn(call_id)
            except Exception as exc:
                logger.error("Redis record_user_turn failed for call %s: %s", call_id, exc)

        # ── Track 3: stream + sentence detection ─────────────────────
        response_text = await _stream_to_first_sentence(
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            temperature=temperature,
            http_client=http_client,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("LLM call failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM service error: {exc}",
        ) from exc

    # ── Track 4: topic-lock drift guard ─────────────────────────────
    if not _is_on_topic(response_text):
        logger.warning(
            "Topic drift detected in response for call %s — substituting fallback.",
            call_id,
        )
        response_text = _DRIFT_FALLBACK

    # Retell expects {"response": "<text>"} for retell-llm response engine
    return {"response": response_text}
