"""
OpenRouter LLM service client.
Calls the OpenAI-compatible API at https://openrouter.ai/api/v1.

Two completion modes:
  chat_completion()        — blocking, returns full response string.
                             Used for non-realtime paths (dev-mode, tests).
  chat_completion_stream() — async generator, yields string tokens via SSE.
                             Used by the webhook for sentence-detection + first-sentence flush.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_CHAT_ENDPOINT = f"{OPENROUTER_BASE_URL}/chat/completions"


def _build_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://voice-agent-platform.onrender.com",
        "X-Title": "Voice Agent Platform",
    }


def _build_payload(
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float,
    *,
    stream: bool = False,
) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": temperature,
        "stream": stream,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
    }


# ---------------------------------------------------------------------------
# Blocking variant — kept for non-realtime paths
# ---------------------------------------------------------------------------
async def chat_completion(
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """
    Send a chat completion request to OpenRouter and return the full assistant
    response text.  Falls back to creating a one-shot client if the shared pool
    client is not provided (e.g. during tests or DEV_MODE calls).

    Args:
        model:         OpenRouter model slug, e.g. \"openai/gpt-4o-mini\"
        system_prompt: The agent system prompt (already wrapped by prompt_builder).
        messages:      Conversation history [{\"role\": ..., \"content\": ...}]
        temperature:   Sampling temperature (0.0–2.0).
        http_client:   Optional shared httpx.AsyncClient from app.state.
    """
    payload = _build_payload(model, system_prompt, messages, temperature, stream=False)

    async def _post(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(
            _CHAT_ENDPOINT,
            json=payload,
            headers=_build_headers(),
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[return-value]

    if http_client is not None:
        data = await _post(http_client)
    else:
        # Fallback: one-shot client (tests / DEV_MODE)
        async with httpx.AsyncClient() as client:
            data = await _post(client)

    try:
        return data["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response structure: %s", data)
        raise ValueError(f"Could not parse OpenRouter response: {data}") from exc


# ---------------------------------------------------------------------------
# Streaming variant — for webhook sentence-detection pipeline
# ---------------------------------------------------------------------------
async def chat_completion_stream(
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    http_client: httpx.AsyncClient | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream a chat completion from OpenRouter, yielding individual text tokens
    as they arrive via Server-Sent Events (SSE).

    The caller (webhook.py) accumulates tokens and applies sentence-detection
    to flush the first complete sentence to Retell immediately, enabling TTS
    synthesis to begin before the full LLM response is done.

    Args:
        model:         OpenRouter model slug.
        system_prompt: Already built by prompt_builder.
        messages:      OpenAI-format conversation history.
        temperature:   Sampling temperature.
        http_client:   Shared AsyncClient from app.state (preferred).

    Yields:
        Non-empty string tokens from the LLM delta stream.
    """
    payload = _build_payload(model, system_prompt, messages, temperature, stream=True)

    async def _stream(client: httpx.AsyncClient) -> AsyncGenerator[str, None]:
        async with client.stream(
            "POST",
            _CHAT_ENDPOINT,
            json=payload,
            headers=_build_headers(),
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        ) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                # SSE lines arrive as "data: {json}" or "data: [DONE]"
                if not raw_line.startswith("data: "):
                    continue
                data_str = raw_line[len("data: "):]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta: str = (
                        chunk["choices"][0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    # Malformed chunk — skip silently, don't break the stream
                    logger.debug("Skipped malformed SSE chunk: %r", raw_line)
                    continue

    if http_client is not None:
        async for token in _stream(http_client):
            yield token
    else:
        async with httpx.AsyncClient() as client:
            async for token in _stream(client):
                yield token
