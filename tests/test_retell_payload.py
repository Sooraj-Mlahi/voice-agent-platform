"""
tests/test_retell_payload.py

Verifies that create_retell_agent() and update_retell_agent() send the correct
VAD/barge-in and silence-handling fields to the Retell API.

Strategy
────────
We intercept the outbound httpx POST/PATCH calls using respx (transport-level
mock) so no real HTTP connections are made. We then assert on the *json body*
that was sent — confirming every Track 2 / Track 4 field is present and correct.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import respx

from app.config import settings
from app.services.retell import create_retell_agent, update_retell_agent

# ---------------------------------------------------------------------------
# Fake Retell API responses
# ---------------------------------------------------------------------------
_FAKE_LLM_RESPONSE = {"llm_id": "llm-test-123"}
_FAKE_AGENT_RESPONSE = {
    "agent_id": "agent-test-456",
    "agent_name": "Test Agent",
    "llm_id": "llm-test-123",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _captured_agent_payload(respx_calls) -> dict:
    """Extract the JSON body from the last /create-agent or /update-agent call."""
    for call in reversed(respx_calls):
        url = str(call.request.url)
        if "create-agent" in url or "update-agent" in url:
            return json.loads(call.request.content)
    raise AssertionError("No agent create/update call found in respx mock calls")


# ---------------------------------------------------------------------------
# create_retell_agent — VAD / barge-in fields
# ---------------------------------------------------------------------------

@respx.mock
async def test_create_agent_interruption_sensitivity():
    """interruption_sensitivity must equal settings.interruption_sensitivity (0.9)."""
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(
        agent_name="Test Agent",
        system_prompt="Help patients book appointments.",
    )

    payload = _captured_agent_payload(respx.calls)
    assert payload["interruption_sensitivity"] == settings.interruption_sensitivity, (
        f"Expected {settings.interruption_sensitivity}, got {payload.get('interruption_sensitivity')}"
    )


@respx.mock
async def test_create_agent_backchannel_enabled():
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(agent_name="Test Agent", system_prompt="Hello.")

    payload = _captured_agent_payload(respx.calls)
    assert payload["enable_backchannel"] is True


@respx.mock
async def test_create_agent_backchannel_frequency():
    """backchannel_frequency must equal settings.backchannel_frequency (0.45)."""
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(agent_name="Test Agent", system_prompt="Hello.")

    payload = _captured_agent_payload(respx.calls)
    assert payload["backchannel_frequency"] == settings.backchannel_frequency, (
        f"Expected {settings.backchannel_frequency}, got {payload.get('backchannel_frequency')}"
    )


@respx.mock
async def test_create_agent_backchannel_words_excludes_uh_huh():
    """
    'Uh-huh' must NOT be in backchannel_words — it sounds anxious at scale.
    The UPDATE_PLAN explicitly calls this out.
    """
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(agent_name="Test Agent", system_prompt="Hello.")

    payload = _captured_agent_payload(respx.calls)
    words = [w.lower() for w in payload.get("backchannel_words", [])]
    assert "uh-huh" not in words, "'Uh-huh' must be excluded from backchannel_words"


@respx.mock
async def test_create_agent_backchannel_words_content():
    """Approved backchannel words must be present."""
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(agent_name="Test Agent", system_prompt="Hello.")

    payload = _captured_agent_payload(respx.calls)
    words = payload.get("backchannel_words", [])
    assert len(words) >= 1, "backchannel_words must not be empty"
    # At least one approved word must be present
    approved = {"Got it.", "Sure.", "One moment."}
    assert any(w in approved for w in words), (
        f"None of the approved backchannel words found in {words}"
    )


# ---------------------------------------------------------------------------
# create_retell_agent — silence / Track 4 fields
# ---------------------------------------------------------------------------

@respx.mock
async def test_create_agent_reminder_trigger_ms_maps_from_timeout():
    """reminder_trigger_ms = silence_timeout_seconds * 1000."""
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    timeout_s = 15
    await create_retell_agent(
        agent_name="Test Agent",
        system_prompt="Hello.",
        silence_timeout_seconds=timeout_s,
    )

    payload = _captured_agent_payload(respx.calls)
    assert payload["reminder_trigger_ms"] == timeout_s * 1000


@respx.mock
async def test_create_agent_end_call_silence_ms_is_600000():
    """end_call_after_silence_ms must be 600_000 (10 min) — webhook owns hang-up."""
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(agent_name="Test Agent", system_prompt="Hello.")

    payload = _captured_agent_payload(respx.calls)
    assert payload["end_call_after_silence_ms"] == 600_000


@respx.mock
async def test_create_agent_reminder_max_count():
    """reminder_max_count must equal settings.max_silence_prompts (2)."""
    respx.post("https://api.retellai.com/create-retell-llm").mock(
        return_value=httpx.Response(200, json=_FAKE_LLM_RESPONSE)
    )
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(agent_name="Test Agent", system_prompt="Hello.")

    payload = _captured_agent_payload(respx.calls)
    assert payload["reminder_max_count"] == settings.max_silence_prompts


# ---------------------------------------------------------------------------
# create_retell_agent — LLM payload (Track 1: prompt wrapping)
# ---------------------------------------------------------------------------

@respx.mock
async def test_create_agent_llm_receives_wrapped_prompt():
    """The LLM creation call must receive a prompt that contains the prosody header."""
    llm_payloads: list[dict] = []

    def capture_llm(request: httpx.Request) -> httpx.Response:
        llm_payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_FAKE_LLM_RESPONSE)

    respx.post("https://api.retellai.com/create-retell-llm").mock(side_effect=capture_llm)
    respx.post("https://api.retellai.com/create-agent").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await create_retell_agent(
        agent_name="Test Agent",
        system_prompt="Help with dental bookings.",
    )

    assert llm_payloads, "No LLM creation call was made"
    sent_prompt = llm_payloads[0].get("general_prompt", "")
    assert "Voice Delivery Instructions" in sent_prompt, (
        "Prompt sent to Retell LLM is not wrapped — prompt_builder not applied"
    )
    assert "Strict Operating Rules" in sent_prompt, (
        "Behavioral footer missing from LLM prompt"
    )


# ---------------------------------------------------------------------------
# update_retell_agent — VAD fields also patched on update
# ---------------------------------------------------------------------------

@respx.mock
async def test_update_agent_includes_vad_fields():
    """
    update_retell_agent must include interruption_sensitivity and backchannel
    fields so legacy agents are upgraded without a full recreate.
    """
    respx.patch("https://api.retellai.com/update-agent/agent-legacy").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    await update_retell_agent(
        retell_agent_id="agent-legacy",
        system_prompt="Legacy prompt.",
    )

    update_call = next(
        c for c in respx.calls if "update-agent" in str(c.request.url)
    )
    payload = json.loads(update_call.request.content)

    assert "interruption_sensitivity" in payload
    assert "enable_backchannel" in payload
    assert "backchannel_frequency" in payload
    assert "reminder_trigger_ms" in payload
    assert "end_call_after_silence_ms" in payload


@respx.mock
async def test_update_agent_reminder_trigger_maps_from_timeout():
    respx.patch("https://api.retellai.com/update-agent/agent-legacy").mock(
        return_value=httpx.Response(200, json=_FAKE_AGENT_RESPONSE)
    )

    timeout_s = 20
    await update_retell_agent(
        retell_agent_id="agent-legacy",
        system_prompt="Legacy prompt.",
        silence_timeout_seconds=timeout_s,
    )

    update_call = next(
        c for c in respx.calls if "update-agent" in str(c.request.url)
    )
    payload = json.loads(update_call.request.content)
    assert payload["reminder_trigger_ms"] == timeout_s * 1000
