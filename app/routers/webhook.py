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
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status

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
    """Retrieve agent config from Supabase customers and agent_configs tables."""
    db = get_supabase()
    # Find the customer ID first
    cust_result = (
        db.table("customers")
        .select("id, reseller_id")
        .eq("retell_agent_id", agent_id)
        .single()
        .execute()
    )
    
    if not cust_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No customer found for agent_id={agent_id}",
        )
        
    customer_id = cust_result.data["id"]
    reseller_id = cust_result.data["reseller_id"]
    
    # Now get the config
    config_result = (
        db.table("agent_configs")
        .select("*")
        .eq("customer_id", customer_id)
        .single()
        .execute()
    )
    
    config = config_result.data if config_result.data else {}
    return {
        "customer_id": customer_id,
        "reseller_id": reseller_id,
        "agent_config": config
    }


async def _log_call_to_supabase(
    call: dict[str, Any],
    customer_id: str,
) -> None:
    """Insert a completed call record into the calls table."""
    db = get_supabase()
    
    # Map Retell transcripts/call info into the new `calls` schema
    transcript_obj = call.get("transcript_object", [])
    transcript = call.get("transcript", "")
    duration = call.get("duration_ms")
    duration_seconds = (duration // 1000) if duration else None
    
    call_id = call.get("call_id")
    caller_number = call.get("from_number")
    
    # Try to map retell disconnection reasons or outcomes
    # Retell typically has "disconnection_reason"
    outcome = "completed"
    disc_reason = call.get("disconnection_reason", "")
    if "transfer" in disc_reason.lower():
        outcome = "transferred"
    elif "voicemail" in disc_reason.lower():
        outcome = "voicemail"
    elif "drop" in disc_reason.lower():
        outcome = "dropped"
    elif "no_answer" in disc_reason.lower() or "timeout" in disc_reason.lower():
        outcome = "no_answer"
        
    started_at_ms = call.get("start_timestamp")
    ended_at_ms = call.get("end_timestamp")
    
    started_at = None
    ended_at = None
    if started_at_ms:
        started_at = datetime.fromtimestamp(started_at_ms / 1000.0, tz=timezone.utc).isoformat()
    if ended_at_ms:
        ended_at = datetime.fromtimestamp(ended_at_ms / 1000.0, tz=timezone.utc).isoformat()

    record = {
        "customer_id": customer_id,
        "retell_call_id": call_id,
        "caller_number": caller_number,
        "duration_seconds": duration_seconds,
        "outcome": outcome,
        "transcript": transcript,
        "started_at": started_at,
        "ended_at": ended_at,
    }

    # Upsert so duplicate webhook deliveries don't create duplicate rows
    db.table("calls").upsert(record, on_conflict="retell_call_id").execute()
    logger.info("Logged call %s for customer %s", call_id, customer_id)


@router.post("/retell")
async def retell_webhook(payload: RetellWebhookRequest) -> dict[str, Any]:
    """
    Main Retell webhook handler.

    Retell sends POST requests to this endpoint throughout the call lifecycle.
    For LLM-mode calls the platform receives the conversation state and must
    return the next assistant utterance.
    """
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
            await _log_call_to_supabase(call, agent_data["customer_id"])
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
