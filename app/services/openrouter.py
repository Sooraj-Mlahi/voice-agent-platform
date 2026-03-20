"""
OpenRouter LLM service client.
Calls the OpenAI-compatible API at https://openrouter.ai/api/v1.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


async def chat_completion(
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
) -> str:
    """
    Send a chat completion request to OpenRouter and return the assistant
    response text.

    Args:
        model: OpenRouter model slug, e.g. "openai/gpt-4o-mini"
        system_prompt: The agent system prompt injected as the first message.
        messages: Conversation history in OpenAI format
                  [{"role": "user"|"assistant", "content": "..."}]
        temperature: Sampling temperature (0.0–2.0).

    Returns:
        The text content of the first choice.
    """
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
    }

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://voice-agent-platform.onrender.com",
        "X-Title": "Voice Agent Platform",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response structure: %s", data)
        raise ValueError(f"Could not parse OpenRouter response: {data}") from exc
