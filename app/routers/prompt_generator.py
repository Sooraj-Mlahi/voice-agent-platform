"""
POST /api/generate-prompt

Generates a voice-optimised system prompt for a Retell AI agent using
OpenRouter (gpt-4o-mini).  The API key stays server-side; the browser
never sees it.

Request:
    {
        "agent_type":    "real_estate",          # see AGENT_TYPE_LABELS
        "company_name":  "Apex Realty",          # shown in persona
        "extra_details": "focus on rentals ..."  # optional free text
    }

Response:
    { "system_prompt": "<generated text>" }

Auth: JWT required (same as all /api/* routes).
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

from app.auth import get_current_reseller
from app.services import openrouter


def _get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the shared httpx client from app.state (avoids circular import)."""
    return request.app.state.http_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Prompt Generator"])

# ---------------------------------------------------------------------------
# Supported agent types — the frontend renders these as a dropdown.
# Values are sent in the request body; labels are for documentation only.
# ---------------------------------------------------------------------------
AGENT_TYPE_LABELS: dict[str, str] = {
    "real_estate":           "Real Estate Agent",
    "customer_support":      "Customer Support",
    "medical_receptionist":  "Medical Receptionist",
    "dental_clinic":         "Dental Clinic Receptionist",
    "restaurant":            "Restaurant Reservations",
    "sales_outreach":        "Sales Outreach",
    "appointment_booking":   "Appointment Booking",
    "property_management":   "Property Management",
    "legal_intake":          "Legal Intake",
    "insurance_inquiry":     "Insurance Inquiry",
    "general":               "General Assistant",
}

# ---------------------------------------------------------------------------
# Meta-prompt — tells the LLM how to write voice-AI system prompts
# ---------------------------------------------------------------------------
_META_SYSTEM = (
    "You are an expert voice AI agent prompt engineer specialising in Retell AI phone agents. "
    "Write a system prompt for a phone voice agent. "
    "Strict rules: "
    "no markdown, no bullet points, no asterisks, no numbered lists, no headers. "
    "Use plain prose paragraphs only. "
    "Keep the total length under 220 words — shorter prompts reduce per-call token cost. "
    "The prompt must include in natural prose: "
    "(1) the agent's persona and name, "
    "(2) the primary task and scope, "
    "(3) the desired tone and communication style, "
    "(4) how to handle questions outside scope — redirect politely without hanging up, "
    "(5) never identify as an AI unless directly asked. "
    "Do NOT include any delivery instructions, bullet rules, or meta-commentary — "
    "write only the actual system prompt text the agent will use."
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class GeneratePromptRequest(BaseModel):
    # Accept both snake_case (agent_type) and camelCase (agentType) from the frontend.
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    agent_type: str
    company_name: str
    extra_details: str = ""

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, v: str) -> str:
        if v not in AGENT_TYPE_LABELS:
            raise ValueError(
                f"Unknown agent_type '{v}'. Valid options: {list(AGENT_TYPE_LABELS)}"
            )
        return v

    @field_validator("company_name")
    @classmethod
    def validate_company_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("company_name must not be empty")
        return v


class GeneratePromptResponse(BaseModel):
    system_prompt: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/generate-prompt",
    response_model=GeneratePromptResponse,
    summary="Generate a voice-optimised system prompt via AI",
)
async def generate_prompt(
    body: GeneratePromptRequest,
    reseller_id: str = Depends(get_current_reseller),
    http_client: httpx.AsyncClient = Depends(_get_http_client),
) -> dict[str, Any]:
    """
    Generate a ready-to-use system prompt for a Retell AI phone agent.

    Uses gpt-4o-mini via OpenRouter — fast (~1 s), cheap (~$0.00015 per call),
    and returns a prompt already optimised for low token count per call turn.
    """
    agent_label = AGENT_TYPE_LABELS[body.agent_type]
    user_msg = (
        f"Agent type: {agent_label}\n"
        f"Company name: {body.company_name}\n"
    )
    if body.extra_details.strip():
        user_msg += f"Additional context: {body.extra_details.strip()}"

    try:
        system_prompt = await openrouter.chat_completion(
            model="openai/gpt-4o-mini",
            system_prompt=_META_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.7,
            http_client=http_client,
        )
    except Exception as exc:
        logger.exception(
            "Prompt generation failed for reseller=%s agent_type=%s: %s",
            reseller_id, body.agent_type, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Prompt generation failed — the AI service returned an error.",
        ) from exc

    return {"system_prompt": system_prompt.strip()}
