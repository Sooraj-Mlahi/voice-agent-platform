"""
Retell AI API service client.
Wraps all calls to https://api.retellai.com using httpx.
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


async def create_retell_agent(
    agent_name: str,
    system_prompt: str,
    voice_id: str = "11labs-Adrian",
    language: str = "en-US",
) -> dict[str, Any]:
    """Create a new Retell agent and return the API response."""
    payload = {
        "agent_name": agent_name,
        "response_engine": {
            "type": "retell-llm",
            "llm_websocket_url": None,  # will be handled by retell-llm type
        },
        "voice_id": voice_id,
        "language": language,
        "general_prompt": system_prompt,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{RETELL_BASE_URL}/create-agent",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


async def update_retell_agent(
    retell_agent_id: str,
    system_prompt: str,
    voice_id: str = "11labs-Adrian",
    language: str = "en-US",
) -> dict[str, Any]:
    """Patch an existing Retell agent configuration."""
    payload = {
        "voice_id": voice_id,
        "language": language,
        "general_prompt": system_prompt,
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
