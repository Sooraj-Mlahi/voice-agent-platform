"""
POST /webhook/retell

Handles real-time Retell AI webhook events:
  - call_started  : acknowledge
  - call_ended    : log transcript to Supabase
  - call_analyzed : (optional post-call analytics hook)
  - default       : LLM response via OpenRouter
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.database import get_supabase
from app.models import RetellWebhookRequest
from app.services import openrouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["Webhook"])


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


async def _fetch_agent_config(agent_id: str) -> dict[str, Any]:
    """Retrieve agent config from Supabase customers table."""
    db = get_supabase()
    result = (
        db.table("customers")
        .select("agent_config, reseller_id")
        .eq("retell_agent_id", agent_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No customer found for agent_id={agent_id}",
        )
    return result.data


async def _log_call_to_supabase(
    call: dict[str, Any],
    agent_id: str,
    reseller_id: str,
) -> None:
    """Insert a completed call record into the call_logs table."""
    db = get_supabase()
    transcript = call.get("transcript", [])
    duration = call.get("duration_ms")
    duration_seconds = (duration // 1000) if duration else None

    record = {
        "call_id": call.get("call_id"),
        "agent_id": agent_id,
        "reseller_id": reseller_id,
        "transcript": transcript,
        "duration_seconds": duration_seconds,
        "started_at": call.get("start_timestamp"),
        "ended_at": call.get("end_timestamp"),
    }

    # Upsert so duplicate webhook deliveries don't create duplicate rows
    db.table("call_logs").upsert(record, on_conflict="call_id").execute()
    logger.info("Logged call %s for reseller %s", call.get("call_id"), reseller_id)


@router.post("/retell")
async def retell_webhook(request: Request) -> dict[str, Any]:
    """
    Main Retell webhook handler.

    Retell sends POST requests to this endpoint throughout the call lifecycle.
    For LLM-mode calls the platform receives the conversation state and must
    return the next assistant utterance.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from exc

    payload = RetellWebhookRequest(**body)
    event = payload.event
    call = payload.call
    agent_id: str = call.get("agent_id", "")

    logger.info("Received Retell event=%s call_id=%s", event, call.get("call_id"))

    # ------------------------------------------------------------------
    # call_started — just acknowledge
    # ------------------------------------------------------------------
    if event == "call_started":
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # call_ended — log transcript to Supabase
    # ------------------------------------------------------------------
    if event in ("call_ended", "call_analyzed"):
        try:
            agent_data = await _fetch_agent_config(agent_id)
            await _log_call_to_supabase(call, agent_id, agent_data["reseller_id"])
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
    # LLM response event — fetch config and call OpenRouter
    # ------------------------------------------------------------------
    try:
        agent_data = await _fetch_agent_config(agent_id)
        config: dict[str, Any] = agent_data.get("agent_config", {})

        system_prompt: str = config.get("system_prompt", "You are a helpful assistant.")
        model: str = config.get("model", "openai/gpt-4o-mini")
        temperature: float = float(config.get("temperature", 0.7))

        messages = _extract_transcript(call)

        response_text = await openrouter.chat_completion(
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            temperature=temperature,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("LLM call failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM service error: {exc}",
        ) from exc

    # Retell expects {"response": "<text>"} for response_engine LLM calls
    return {"response": response_text}
