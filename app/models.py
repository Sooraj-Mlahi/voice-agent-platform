"""
Pydantic models for request/response validation across the API.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Retell Webhook models
# ---------------------------------------------------------------------------

class RetellMessage(BaseModel):
    role: str  # "agent" | "user"
    content: str


class RetellWebhookRequest(BaseModel):
    """
    Retell sends a payload like:
    {
        "event": "call_started" | "call_ended" | "call_analyzed",
        "call": { ... }
    }
    For real-time LLM events the call object includes transcript.
    """
    event: str
    call: dict[str, Any]


# ---------------------------------------------------------------------------
# Customer models
# ---------------------------------------------------------------------------

class AgentConfig(BaseModel):
    system_prompt: str | None = Field(default=None)
    voice_id: str | None = Field(default="11labs-Adrian")
    language: str = Field(default="en-US")
    llm_model: str = Field(default="gpt-4o")
    business_hours: dict[str, Any] | None = Field(default=None)
    escalation_phone: str | None = Field(default=None)
    calendar_webhook_url: str | None = Field(default=None)
    crm_webhook_url: str | None = Field(default=None)
    faq_knowledge_base: str | None = Field(default=None)
    recording_enabled: bool = Field(default=False)


class CreateCustomerRequest(BaseModel):
    name: str = Field(..., min_length=1)
    billing_email: str = Field(..., description="Customer billing email")
    phone_number: str | None = Field(default=None, description="E.164 format")
    plan: str = Field(default="starter")
    status: str = Field(default="active")
    retell_agent_id: str | None = Field(default=None, description="If provided, links to an existing Retell Agent instead of creating a new one.")
    agent_config: AgentConfig


class UpdateAgentConfigRequest(BaseModel):
    agent_config: AgentConfig


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CustomerResponse(BaseModel):
    id: str
    reseller_id: str
    name: str
    billing_email: str
    phone_number: str | None
    plan: str
    status: str
    retell_agent_id: str | None
    created_at: str


class CallLogResponse(BaseModel):
    id: str
    customer_id: str
    retell_call_id: str | None
    caller_number: str | None
    duration_seconds: int | None
    outcome: str | None
    transcript: str | None
    started_at: str | None
    ended_at: str | None
    cost_usd: float | None
