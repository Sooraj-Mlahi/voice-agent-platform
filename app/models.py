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
    system_prompt: str = Field(..., description="System prompt used for the LLM")
    voice_id: str = Field(default="11labs-Adrian", description="Retell voice ID")
    language: str = Field(default="en-US")
    model: str = Field(default="openai/gpt-4o-mini", description="OpenRouter model slug")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class CreateCustomerRequest(BaseModel):
    name: str = Field(..., min_length=1)
    phone_number: str = Field(..., description="E.164 format, e.g. +14155552671")
    business_name: str = Field(default="")
    agent_config: AgentConfig


class UpdateAgentConfigRequest(BaseModel):
    agent_config: AgentConfig


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CustomerResponse(BaseModel):
    id: str
    name: str
    phone_number: str
    business_name: str
    retell_agent_id: str | None
    reseller_id: str
    agent_config: dict[str, Any]
    created_at: str


class CallLogResponse(BaseModel):
    id: str
    call_id: str
    agent_id: str
    customer_id: str | None
    reseller_id: str
    transcript: list[dict[str, Any]]
    duration_seconds: int | None
    started_at: str | None
    ended_at: str | None
    created_at: str
