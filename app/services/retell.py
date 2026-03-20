"""
Retell AI API service client.
Wraps all calls to https://api.retellai.com using httpx.

Retell agent creation requires two steps:
  1. POST /create-retell-llm  → get llm_id
  2. POST /create-agent       → link agent to llm_id
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RETELL_BASE_URL = "https://api.retellai.com"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.retell_api_key}",
        "Content-Type": "application/json",
    }


async def _create_retell_llm(
    system_prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
) -> str:
    """
    Step 1: Create a Retell LLM and return its llm_id.
    The LLM holds the system prompt and model config.
    """
    payload = {
        "general_prompt": system_prompt,
        "model": model,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RETELL_BASE_URL}/create-retell-llm",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()["llm_id"]


async def create_retell_agent(
    agent_name: str,
    system_prompt: str,
    voice_id: str = "11labs-Adrian",
    language: str = "en-US",
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
) -> dict[str, Any]:
    """
    Create a new Retell agent (two-step: LLM → Agent).
    Returns the full agent API response including agent_id.
    """
    # Step 1 — create LLM
    llm_id = await _create_retell_llm(system_prompt, model, temperature)

    # Step 2 — create agent linked to LLM
    payload = {
        "agent_name": agent_name,
        "response_engine": {
            "type": "retell-llm",
            "llm_id": llm_id,
        },
        "voice_id": voice_id,
        "language": language,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RETELL_BASE_URL}/create-agent",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
        # Store llm_id alongside agent data for future updates
        data["llm_id"] = llm_id
        return data


async def update_retell_agent(
    retell_agent_id: str,
    system_prompt: str,
    voice_id: str = "11labs-Adrian",
    language: str = "en-US",
    llm_id: str | None = None,
) -> dict[str, Any]:
    """
    Update an existing Retell agent.
    - If llm_id is provided, also patches the LLM's system prompt.
    - Patches voice/language on the agent itself.
    """
    # Patch the LLM prompt if we have the llm_id
    if llm_id:
        async with httpx.AsyncClient(timeout=30.0) as client:
            llm_response = await client.patch(
                f"{RETELL_BASE_URL}/update-retell-llm/{llm_id}",
                json={"general_prompt": system_prompt},
                headers=_headers(),
            )
            llm_response.raise_for_status()

    # Patch agent voice/language
    payload: dict[str, Any] = {
        "voice_id": voice_id,
        "language": language,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{RETELL_BASE_URL}/update-agent/{retell_agent_id}",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


async def get_retell_call(call_id: str) -> dict[str, Any]:
    """Fetch call details from Retell."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{RETELL_BASE_URL}/get-call/{call_id}",
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


async def get_retell_agent(agent_id: str) -> dict[str, Any]:
    """Fetch agent details from Retell."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{RETELL_BASE_URL}/get-agent/{agent_id}",
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()
