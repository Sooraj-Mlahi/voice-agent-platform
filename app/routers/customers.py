"""
POST /api/customers  — create customer + Retell agent
GET  /api/customers  — list customers for the reseller
PUT  /api/customers/{customer_id}/config — update agent config
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import get_current_reseller
from app.database import get_supabase
from app.models import CreateCustomerRequest, UpdateAgentConfigRequest
from app.services import retell

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Customers"])
public_router = APIRouter(prefix="/public", tags=["Public"])


# ---------------------------------------------------------------------------
# POST /api/customers
# ---------------------------------------------------------------------------

@router.post("/customers", status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CreateCustomerRequest,
    reseller_id: str = Depends(get_current_reseller),
) -> dict[str, Any]:
    """
    1. Create a Retell AI agent with the supplied config.
    2. Persist the customer record (with retell_agent_id) to Supabase.
    """
    db = get_supabase()

    retell_agent_id = body.retell_agent_id

    # --- Step 1: Create Retell agent (if not provided) ---
    if not retell_agent_id:
        try:
            retell_response = await retell.create_retell_agent(
                agent_name=body.name,
                system_prompt=body.agent_config.system_prompt or "",
                voice_id=body.agent_config.voice_id or "11labs-Adrian",
                language=body.agent_config.language,
                model=body.agent_config.llm_model,
                prosody_style=body.agent_config.prosody_style,
                silence_timeout_seconds=body.agent_config.silence_timeout_seconds,
                custom_vocabulary=body.agent_config.custom_vocabulary or None,
            )
            retell_agent_id = retell_response.get("agent_id", "")
        except Exception as exc:
            err_msg = str(exc)
            if hasattr(exc, "response") and hasattr(exc.response, "text"):
                err_msg = f"{exc} - Body: {exc.response.text}"
                
            logger.exception("Retell agent creation failed: %s", err_msg)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to create Retell agent. Retell said: {err_msg}",
            ) from exc

    # --- Step 2: Persist to Supabase ---
    record = {
        "name": body.name,
        "phone_number": body.phone_number,
        "billing_email": body.billing_email,
        "plan": body.plan,
        "status": body.status,
        "reseller_id": reseller_id,
        "retell_agent_id": retell_agent_id,
    }

    try:
        result = db.table("customers").insert(record).execute()
    except Exception as exc:
        logger.exception("Supabase insert failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save customer: {exc}",
        ) from exc

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Customer insert returned no data",
        )

    customer_data = result.data[0]
    customer_id = customer_data["id"]

    # --- Step 3: Persist Agent Config to agent_configs table ---
    config_record = {
        "customer_id": customer_id,
        "system_prompt": body.agent_config.system_prompt,
        "voice_id": body.agent_config.voice_id,
        "language": body.agent_config.language,
        "llm_model": body.agent_config.llm_model,
        "business_hours": body.agent_config.business_hours,
        "escalation_phone": body.agent_config.escalation_phone,
        "calendar_webhook_url": body.agent_config.calendar_webhook_url,
        "crm_webhook_url": body.agent_config.crm_webhook_url,
        "faq_knowledge_base": body.agent_config.faq_knowledge_base,
        "recording_enabled": body.agent_config.recording_enabled,
        # Track 1 — Tonality
        "prosody_style": body.agent_config.prosody_style,
        # Track 4 — Silence / State-bound flow
        "silence_timeout_seconds": body.agent_config.silence_timeout_seconds,
        "max_silence_prompts": body.agent_config.max_silence_prompts,
        # STT — custom vocabulary
        "custom_vocabulary": body.agent_config.custom_vocabulary,
    }

    try:
        db.table("agent_configs").insert(config_record).execute()
    except Exception as exc:
        logger.error("Failed to insert agent config: %s", exc)
        # We don't fail the whole request if this fails, but it's bad state
        pass

    return customer_data


# ---------------------------------------------------------------------------
# GET /api/customers
# ---------------------------------------------------------------------------

@router.get("/customers", response_model=list[dict[str, Any]])
async def list_customers(
    reseller_id: str = Depends(get_current_reseller),
) -> list[dict[str, Any]]:
    """Return all customers belonging to the authenticated reseller."""
    try:
        db = get_supabase()
        result = (
            db.table("customers")
            .select("*")
            .eq("reseller_id", reseller_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.exception("Failed to fetch customers: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve customers",
        ) from exc


# ---------------------------------------------------------------------------
# PUT /api/customers/{customer_id}/config
# ---------------------------------------------------------------------------

@router.put("/customers/{customer_id}/config")
async def update_agent_config(
    customer_id: str,
    body: UpdateAgentConfigRequest,
    reseller_id: str = Depends(get_current_reseller),
) -> dict[str, Any]:
    """
    1. Update the agent_config column in Supabase.
    2. Patch the corresponding Retell agent.
    """
    db = get_supabase()

    # --- Fetch existing customer (scoped to reseller) ---
    try:
        result = (
            db.table("customers")
            .select("id, retell_agent_id")
            .eq("id", customer_id)
            .eq("reseller_id", reseller_id)
            .single()
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        ) from exc

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found or access denied",
        )

    retell_agent_id: str = result.data.get("retell_agent_id", "")
    config = body.agent_config

    if not retell_agent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer does not have an associated Retell agent to update.",
        )

    # We must fetch the llm_id dynamically from the Retell API
    llm_id = None
    try:
        agent_data = await retell.get_retell_agent(retell_agent_id)
        # Retell API v2 places llm_websocket_url linking to the LLM ID 
        # But if we created it via create-agent, Retell returns the response_engine
        response_engine = agent_data.get("response_engine", {})
        llm_id = response_engine.get("llm_id")
    except Exception as exc:
        logger.warning("Could not fetch Retell Agent detail to find llm_id: %s", exc)

    # --- Step 1: Update Supabase agent_configs table ---
    config_update = {
        "system_prompt": config.system_prompt,
        "voice_id": config.voice_id,
        "language": config.language,
        "llm_model": config.llm_model,
        "business_hours": config.business_hours,
        "escalation_phone": config.escalation_phone,
        "calendar_webhook_url": config.calendar_webhook_url,
        "crm_webhook_url": config.crm_webhook_url,
        "faq_knowledge_base": config.faq_knowledge_base,
        "recording_enabled": config.recording_enabled,
        # Track 1 — Tonality
        "prosody_style": config.prosody_style,
        # Track 4 — Silence / State-bound flow
        "silence_timeout_seconds": config.silence_timeout_seconds,
        "max_silence_prompts": config.max_silence_prompts,
        # STT — custom vocabulary
        "custom_vocabulary": config.custom_vocabulary,
    }
    
    # We remove None keys so we don't accidentally overwrite good data with nulls when patching
    config_update = {k: v for k, v in config_update.items() if v is not None}

    try:
        update_result = (
            db.table("agent_configs")
            .update(config_update)
            .eq("customer_id", customer_id)
            .execute()
        )
    except Exception as exc:
        logger.exception("Supabase agent_configs update failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update agent config in database: {exc}",
        ) from exc

    # --- Step 2: Update Retell agent ---
    if retell_agent_id:
        try:
            await retell.update_retell_agent(
                retell_agent_id=retell_agent_id,
                system_prompt=config.system_prompt or "",
                voice_id=config.voice_id or "11labs-Adrian",
                language=config.language or "en",
                llm_id=llm_id,
            )
        except Exception as exc:
            logger.exception("Retell agent update failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Supabase updated but Retell agent sync failed: {exc}",
            ) from exc

    return update_result.data[0] if update_result.data else {"status": "updated"}

# ---------------------------------------------------------------------------
# POST /api/customers/{customer_id}/web-call
# ---------------------------------------------------------------------------

@router.post("/customers/{customer_id}/web-call")
async def create_web_call_for_customer(
    customer_id: str,
    reseller_id: str = Depends(get_current_reseller),
) -> dict[str, Any]:
    """
    Creates a Retell WebRTC access token for browser-based calling.
    """
    db = get_supabase()
    try:
        result = (
            db.table("customers")
            .select("retell_agent_id")
            .eq("id", customer_id)
            .eq("reseller_id", reseller_id)
            .single()
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query customer: {exc}"
        ) from exc
        
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
        
    agent_id = result.data.get("retell_agent_id")
    if not agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer has no agent")
        
    try:
        return await retell.create_web_call(agent_id)
    except Exception as exc:
        logger.exception("Failed to create web call: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Retell Web Call creation failed: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Public (no auth) — Instant Agent Page endpoints
# GET  /public/agent/:customer_id        → agent display name + voice_id
# POST /public/agent/:customer_id/token  → Retell web-call access token
# ---------------------------------------------------------------------------

@public_router.get("/agent/{customer_id}")
async def public_agent_info(customer_id: str) -> dict[str, Any]:
    """
    Returns minimal public-safe agent metadata for the Instant Agent Page.
    No auth required — only non-sensitive fields are exposed.
    """
    db = get_supabase()
    try:
        result = (
            db.table("customers")
            .select("id, name, retell_agent_id")
            .eq("id", customer_id)
            .single()
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found") from exc

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    return {
        "customer_id": result.data["id"],
        "name": result.data["name"],
        "has_agent": bool(result.data.get("retell_agent_id")),
    }


@public_router.post("/agent/{customer_id}/token")
async def public_agent_token(customer_id: str) -> dict[str, Any]:
    """
    Creates a Retell WebRTC access token for the public Instant Agent Page.
    No auth required — anyone with the customer_id can start a call.
    Rate-limiting should be applied at the infrastructure level (Railway / Vercel).
    """
    db = get_supabase()
    try:
        result = (
            db.table("customers")
            .select("retell_agent_id")
            .eq("id", customer_id)
            .single()
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found") from exc

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    agent_id = result.data.get("retell_agent_id")
    if not agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No agent configured")

    try:
        return await retell.create_web_call(agent_id)
    except Exception as exc:
        logger.exception("Public token creation failed for customer %s: %s", customer_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create call token",
        ) from exc
