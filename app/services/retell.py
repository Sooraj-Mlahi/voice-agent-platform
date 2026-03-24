"""
Retell AI API service client.
Wraps all calls to https://api.retellai.com using the shared httpx.AsyncClient
(from app.state) where available, falling back to one-shot clients otherwise.

Retell agent creation requires two steps:
  1. POST /create-retell-llm  → get llm_id  (system prompt + model config)
  2. POST /create-agent       → link agent to llm_id  (voice, VAD, barge-in)

Track 1 (Tonality):
  - All system prompts are wrapped through prompt_builder.build_prompt() before
    being sent to Retell, ensuring prosody instructions and guardrails are applied.

Track 2 (Barge-in / VAD):
  - create_retell_agent() and update_retell_agent() now include the full set of
    VAD/backchannel parameters (interruption_sensitivity, backchannel_frequency,
    reminder_trigger_ms, etc.) sourced from settings for easy env-level tuning.

Track 4 (Silence handling):
  - silence_timeout_seconds is mapped to reminder_trigger_ms dynamically from
    the per-customer AgentConfig value.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.services.prompt_builder import build_prompt

logger = logging.getLogger(__name__)

RETELL_BASE_URL = "https://api.retellai.com"

# ---------------------------------------------------------------------------
# Model name translation — OpenRouter format → Retell native format
# ---------------------------------------------------------------------------
# AgentConfig stores OpenRouter-style names (e.g. "openai/gpt-4o-mini") so the
# frontend and Supabase use a consistent namespace. Retell's /create-retell-llm
# endpoint uses its own registry. This map translates at the service boundary.
_OPENROUTER_TO_RETELL: dict[str, str] = {
    # OpenAI
    "openai/gpt-4o-mini":        "gpt-4o-mini",
    "openai/gpt-4o":             "gpt-4o",
    "openai/gpt-4.1":            "gpt-4.1",
    "openai/gpt-4.1-mini":       "gpt-4.1-mini",
    "openai/gpt-4.1-nano":       "gpt-4.1-nano",
    # Anthropic
    "anthropic/claude-3-haiku":          "claude-4.5-haiku",
    "anthropic/claude-3-5-haiku":        "claude-4.5-haiku",
    "anthropic/claude-3-sonnet":         "claude-4.5-sonnet",
    "anthropic/claude-3-5-sonnet":       "claude-4.5-sonnet",
    "anthropic/claude-3-5-sonnet-20241022": "claude-4.5-sonnet",
    "anthropic/claude-sonnet-4-5":       "claude-4.5-sonnet",
    "anthropic/claude-sonnet-4-6":       "claude-4.6-sonnet",
    # Google
    "google/gemini-flash-1.5":           "gemini-2.0-flash",
    "google/gemini-2.0-flash":           "gemini-2.0-flash",
    "google/gemini-2.5-flash":           "gemini-2.5-flash",
}


def _retell_model(openrouter_model: str) -> str:
    """Translate an OpenRouter model name to a Retell-native model name.

    Falls back to the input unchanged so that callers already using Retell
    names (e.g. "gpt-4o-mini") continue to work without modification.
    """
    return _OPENROUTER_TO_RETELL.get(openrouter_model, openrouter_model)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.retell_api_key}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Internal helper — get a usable httpx client
# ---------------------------------------------------------------------------
def _client(http_client: httpx.AsyncClient | None) -> httpx.AsyncClient | None:
    """Return the injected shared client or None (caller handles fallback)."""
    return http_client


# ---------------------------------------------------------------------------
# Step 1 — Create Retell LLM
# ---------------------------------------------------------------------------
async def _create_retell_llm(
    system_prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """
    Create a Retell LLM and return its llm_id.
    The system_prompt must already be wrapped through prompt_builder.build_prompt()
    before being passed here.
    """
    payload = {
        "general_prompt": system_prompt,
        "model": _retell_model(model),
        "temperature": temperature,
    }

    async def _post(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(
            f"{RETELL_BASE_URL}/create-retell-llm",
            json=payload,
            headers=_headers(),
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[return-value]

    if http_client is not None:
        data = await _post(http_client)
    else:
        async with httpx.AsyncClient() as client:
            data = await _post(client)

    return data["llm_id"]


# ---------------------------------------------------------------------------
# Public — Create Retell Agent (two-step: LLM → Agent)
# ---------------------------------------------------------------------------
async def create_retell_agent(
    agent_name: str,
    system_prompt: str,
    voice_id: str = "11labs-Adrian",
    language: str = "en-US",
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    prosody_style: str = "warm-conversational",
    silence_timeout_seconds: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Create a new Retell agent (two-step: LLM → Agent).

    Track 1: system_prompt is wrapped through prompt_builder before creating the LLM.
    Track 2: full VAD/barge-in configuration is applied to the agent payload.
    Track 4: silence_timeout_seconds maps to reminder_trigger_ms on the agent.

    Returns the full agent API response including agent_id and llm_id.
    """
    # ── Track 1: wrap prompt ─────────────────────────────────────────────
    wrapped_prompt = build_prompt(system_prompt, style=prosody_style)

    # ── Step 1: create LLM ──────────────────────────────────────────────
    llm_id = await _create_retell_llm(
        system_prompt=wrapped_prompt,
        model=model,
        temperature=temperature,
        http_client=http_client,
    )

    # ── Step 2: create agent ────────────────────────────────────────────
    # Track 2 / Track 4 — VAD, barge-in, silence reminder
    payload: dict[str, Any] = {
        "agent_name": agent_name,
        "response_engine": {
            "type": "retell-llm",
            "llm_id": llm_id,
        },
        "voice_id": voice_id,
        "language": language,

        # ── Barge-in & VAD (Track 2) ─────────────────────────────────
        # interruption_sensitivity: how aggressively Retell's VAD detects
        # user speech to cut the agent mid-sentence (0.0–1.0).
        "interruption_sensitivity": settings.interruption_sensitivity,

        # Backchannel: agent interjects short affirmations while user speaks.
        # Frequency at 0.45 (~45%) — natural presence without sounding anxious.
        # "Uh-huh" is excluded; it sounds nervous when repeated.
        "enable_backchannel": True,
        "backchannel_frequency": settings.backchannel_frequency,
        "backchannel_words": ["Got it.", "Sure.", "One moment."],

        # ── Silence handling (Track 4) ───────────────────────────────
        # reminder_trigger_ms: how long Retell waits before firing a silence
        # reminder callback. Maps from per-customer silence_timeout_seconds.
        "reminder_trigger_ms": silence_timeout_seconds * 1000,

        # reminder_max_count: Retell fires at most N reminders before it
        # stops — our webhook handles the actual response text and hang-up.
        "reminder_max_count": settings.max_silence_prompts,

        # Let our webhook manage the final hang-up — don't let Retell
        # auto-disconnect too early (10 minutes = generous ceiling).
        "end_call_after_silence_ms": 600_000,
    }

    if settings.webhook_base_url:
        payload["webhook_url"] = (
            f"{settings.webhook_base_url.rstrip('/')}/webhook/retell"
        )

    async def _post(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(
            f"{RETELL_BASE_URL}/create-agent",
            json=payload,
            headers=_headers(),
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[return-value]

    if http_client is not None:
        data = await _post(http_client)
    else:
        async with httpx.AsyncClient() as client:
            data = await _post(client)

    # Carry llm_id through so update_retell_agent can patch the LLM directly.
    data["llm_id"] = llm_id
    return data


# ---------------------------------------------------------------------------
# Public — Update Retell Agent
# ---------------------------------------------------------------------------
async def update_retell_agent(
    retell_agent_id: str,
    system_prompt: str,
    voice_id: str = "11labs-Adrian",
    language: str = "en-US",
    llm_id: str | None = None,
    prosody_style: str = "warm-conversational",
    silence_timeout_seconds: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Update an existing Retell agent.

    Track 1: system_prompt is re-wrapped through prompt_builder.
    Track 2: VAD/barge-in parameters are patched so legacy agents get upgraded.
    Track 4: silence_timeout_seconds is re-applied via reminder_trigger_ms.
    """
    wrapped_prompt = build_prompt(system_prompt, style=prosody_style)

    # ── Patch the LLM system prompt ─────────────────────────────────────
    if llm_id:
        async def _patch_llm(client: httpx.AsyncClient) -> None:
            resp = await client.patch(
                f"{RETELL_BASE_URL}/update-retell-llm/{llm_id}",
                json={"general_prompt": wrapped_prompt},
                headers=_headers(),
                timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            )
            resp.raise_for_status()

        if http_client is not None:
            await _patch_llm(http_client)
        else:
            async with httpx.AsyncClient() as client:
                await _patch_llm(client)

    # ── Patch agent voice / language + VAD settings ─────────────────────
    agent_payload: dict[str, Any] = {
        "voice_id": voice_id,
        "language": language,
        # Track 2 — keep VAD in sync on updates too
        "interruption_sensitivity": settings.interruption_sensitivity,
        "enable_backchannel": True,
        "backchannel_frequency": settings.backchannel_frequency,
        "backchannel_words": ["Got it.", "Sure.", "One moment."],
        # Track 4
        "reminder_trigger_ms": silence_timeout_seconds * 1000,
        "reminder_max_count": settings.max_silence_prompts,
        "end_call_after_silence_ms": 600_000,
    }

    async def _patch_agent(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.patch(
            f"{RETELL_BASE_URL}/update-agent/{retell_agent_id}",
            json=agent_payload,
            headers=_headers(),
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[return-value]

    if http_client is not None:
        return await _patch_agent(http_client)
    else:
        async with httpx.AsyncClient() as client:
            return await _patch_agent(client)


# ---------------------------------------------------------------------------
# Public — Get Agent / Call details
# ---------------------------------------------------------------------------
async def get_retell_call(
    call_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch call details from Retell."""
    async def _get(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(
            f"{RETELL_BASE_URL}/get-call/{call_id}",
            headers=_headers(),
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[return-value]

    if http_client is not None:
        return await _get(http_client)
    async with httpx.AsyncClient() as client:
        return await _get(client)


async def get_retell_agent(
    agent_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch agent details from Retell."""
    async def _get(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(
            f"{RETELL_BASE_URL}/get-agent/{agent_id}",
            headers=_headers(),
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[return-value]

    if http_client is not None:
        return await _get(http_client)
    async with httpx.AsyncClient() as client:
        return await _get(client)


async def create_web_call(
    agent_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Create a new WebRTC call token for the browser."""
    payload = {"agent_id": agent_id}

    async def _post(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(
            f"{RETELL_BASE_URL}/v2/create-web-call",
            json=payload,
            headers=_headers(),
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[return-value]

    if http_client is not None:
        return await _post(http_client)
    async with httpx.AsyncClient() as client:
        return await _post(client)
