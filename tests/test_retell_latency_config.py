"""
tests/test_retell_latency_config.py

Regression tests that verify every latency-optimisation parameter is present
in the payloads sent to Retell when creating or updating agents.

Why payload inspection rather than live calls
─────────────────────────────────────────────
Retell e2e latency is driven entirely by the Retell platform (LLM inference +
TTS synthesis).  We cannot assert a wall-clock ms value in a unit test.
What we CAN assert is that the configuration that *drives* low latency is
correctly included in every API request:

    LLM payload  — max_tokens (caps output length → shorter TTS)
    Agent payload — responsiveness  (fires sooner after user stops)
                    voice_model     (eleven_turbo_v2_5 is ~40% faster)
                    normalize_for_speech (pre-normalises numbers/dates)

These tests make create_retell_agent / update_retell_agent call a mock httpx
client and capture the JSON body of each request.  If any parameter is ever
accidentally removed, these tests fail immediately.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.retell import create_retell_agent, update_retell_agent
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(responses: list[dict[str, Any]]) -> MagicMock:
    """
    Return a mock httpx.AsyncClient whose .post() / .patch() calls succeed.

    Each call pops the next response dict from `responses`.
    The mock records the JSON body of every call in mock.call_args_list.
    """
    client = MagicMock()
    call_idx = 0

    async def _fake_post(url: str, *, json: dict, **kwargs) -> MagicMock:
        nonlocal call_idx
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = responses[call_idx]
        call_idx += 1
        return resp

    async def _fake_patch(url: str, *, json: dict, **kwargs) -> MagicMock:
        nonlocal call_idx
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = responses[call_idx] if call_idx < len(responses) else {}
        call_idx += 1
        return resp

    client.post = _fake_post
    client.patch = _fake_patch
    return client


# ---------------------------------------------------------------------------
# create_retell_agent — LLM payload assertions
# ---------------------------------------------------------------------------

async def test_create_llm_includes_max_tokens():
    """
    The /create-retell-llm POST must include max_tokens so Retell caps
    output length — the primary lever for reducing TTS synthesis time.
    """
    captured: list[dict] = []

    async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):
        captured.append({"url": url, "json": json})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "create-retell-llm" in url:
            resp.json.return_value = {"llm_id": "llm_test123"}
        else:
            resp.json.return_value = {"agent_id": "agent_test456", "llm_id": "llm_test123"}
        return resp

    mock_client = MagicMock()
    mock_client.post = _fake_post

    await create_retell_agent(
        agent_name="Test Agent",
        system_prompt="You are a helpful assistant.",
        http_client=mock_client,
    )

    llm_calls = [c for c in captured if "create-retell-llm" in c["url"]]
    assert llm_calls, "No /create-retell-llm call was made"
    llm_payload = llm_calls[0]["json"]

    assert "max_tokens" in llm_payload, (
        f"max_tokens missing from LLM payload: {llm_payload}"
    )
    assert llm_payload["max_tokens"] == settings.agent_max_tokens, (
        f"Expected max_tokens={settings.agent_max_tokens}, got {llm_payload['max_tokens']}"
    )


async def test_create_agent_includes_responsiveness():
    """
    The /create-agent POST must include responsiveness=1.0 so the agent
    fires immediately after the user stops speaking.
    """
    captured: list[dict] = []

    async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):
        captured.append({"url": url, "json": json})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "create-retell-llm" in url:
            resp.json.return_value = {"llm_id": "llm_r1"}
        else:
            resp.json.return_value = {"agent_id": "agent_r1", "llm_id": "llm_r1"}
        return resp

    mock_client = MagicMock()
    mock_client.post = _fake_post

    await create_retell_agent(
        agent_name="Test",
        system_prompt="prompt",
        http_client=mock_client,
    )

    agent_calls = [c for c in captured if "create-agent" in c["url"]]
    assert agent_calls, "No /create-agent call was made"
    payload = agent_calls[0]["json"]

    assert "responsiveness" in payload, f"responsiveness missing: {payload}"
    assert payload["responsiveness"] == settings.agent_responsiveness


async def test_create_agent_includes_voice_model():
    """
    The /create-agent POST must include voice_model=eleven_turbo_v2_5 for
    lower-latency TTS synthesis.
    """
    captured: list[dict] = []

    async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):
        captured.append({"url": url, "json": json})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "create-retell-llm" in url:
            resp.json.return_value = {"llm_id": "llm_vm1"}
        else:
            resp.json.return_value = {"agent_id": "agent_vm1", "llm_id": "llm_vm1"}
        return resp

    mock_client = MagicMock()
    mock_client.post = _fake_post

    await create_retell_agent(
        agent_name="Test",
        system_prompt="prompt",
        http_client=mock_client,
    )

    agent_calls = [c for c in captured if "create-agent" in c["url"]]
    payload = agent_calls[0]["json"]

    assert "voice_model" in payload, f"voice_model missing: {payload}"
    assert payload["voice_model"] == settings.agent_voice_model


async def test_create_agent_includes_normalize_for_speech():
    """
    The /create-agent POST must include normalize_for_speech=True so Retell
    pre-normalises numbers/dates/abbreviations before TTS synthesis.
    """
    captured: list[dict] = []

    async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):
        captured.append({"url": url, "json": json})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "create-retell-llm" in url:
            resp.json.return_value = {"llm_id": "llm_nfs1"}
        else:
            resp.json.return_value = {"agent_id": "agent_nfs1", "llm_id": "llm_nfs1"}
        return resp

    mock_client = MagicMock()
    mock_client.post = _fake_post

    await create_retell_agent(
        agent_name="Test",
        system_prompt="prompt",
        http_client=mock_client,
    )

    agent_calls = [c for c in captured if "create-agent" in c["url"]]
    payload = agent_calls[0]["json"]

    assert "normalize_for_speech" in payload, f"normalize_for_speech missing: {payload}"
    assert payload["normalize_for_speech"] is True


# ---------------------------------------------------------------------------
# update_retell_agent — latency params must be kept in sync
# ---------------------------------------------------------------------------

async def test_update_llm_includes_max_tokens():
    """update_retell_agent must patch max_tokens on the LLM too."""
    captured: list[dict] = []

    async def _fake_patch(url: str, *, json: dict, headers: dict, **kwargs):
        captured.append({"url": url, "json": json})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"agent_id": "agent_upd1"}
        return resp

    mock_client = MagicMock()
    mock_client.patch = _fake_patch

    await update_retell_agent(
        retell_agent_id="agent_upd1",
        system_prompt="Updated prompt.",
        llm_id="llm_upd1",
        http_client=mock_client,
    )

    llm_patches = [c for c in captured if "update-retell-llm" in c["url"]]
    assert llm_patches, "No /update-retell-llm PATCH was made"
    assert "max_tokens" in llm_patches[0]["json"], (
        f"max_tokens missing from LLM patch: {llm_patches[0]['json']}"
    )


async def test_update_agent_includes_latency_params():
    """update_retell_agent must include all three agent-level latency params."""
    captured: list[dict] = []

    async def _fake_patch(url: str, *, json: dict, headers: dict, **kwargs):
        captured.append({"url": url, "json": json})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"agent_id": "agent_upd2"}
        return resp

    mock_client = MagicMock()
    mock_client.patch = _fake_patch

    await update_retell_agent(
        retell_agent_id="agent_upd2",
        system_prompt="Updated prompt.",
        llm_id="llm_upd2",
        http_client=mock_client,
    )

    agent_patches = [c for c in captured if "update-agent" in c["url"]]
    assert agent_patches, "No /update-agent PATCH was made"
    payload = agent_patches[0]["json"]

    for key in ("responsiveness", "voice_model", "normalize_for_speech"):
        assert key in payload, f"{key} missing from agent update payload: {payload}"

    assert payload["responsiveness"] == settings.agent_responsiveness
    assert payload["voice_model"] == settings.agent_voice_model
    assert payload["normalize_for_speech"] is True


# ---------------------------------------------------------------------------
# prompt_builder — brevity rule is present and tightened
# ---------------------------------------------------------------------------

def test_prompt_brevity_rule_limits_to_two_sentences():
    """
    build_prompt must inject a brevity rule that caps answers at TWO sentences.
    This keeps LLM output short → reduces TTS synthesis time.
    """
    from app.services.prompt_builder import build_prompt

    prompt = build_prompt("You help customers with orders.")
    assert "TWO sentences maximum" in prompt, (
        "Brevity rule must enforce 'TWO sentences maximum' to keep responses short"
    )


def test_prompt_brevity_rule_one_sentence_for_simple():
    """Simple questions must be capped at ONE sentence."""
    from app.services.prompt_builder import build_prompt

    prompt = build_prompt("You are a support agent.")
    assert "ONE sentence only" in prompt


def test_prompt_brevity_no_three_sentence_cap():
    """
    The old 'TWO to THREE sentences' cap has been tightened to TWO.
    Ensure the loosened wording is gone.
    """
    from app.services.prompt_builder import build_prompt

    prompt = build_prompt("Any prompt.")
    assert "THREE sentences" not in prompt, (
        "THREE-sentence cap found — brevity rule must be tightened to TWO"
    )


# ---------------------------------------------------------------------------
# Config defaults — sanity check that settings are sane
# ---------------------------------------------------------------------------

def test_agent_max_tokens_default_is_reasonable():
    """Default max_tokens must be ≤ 300 to keep responses short."""
    assert settings.agent_max_tokens <= 300, (
        f"agent_max_tokens={settings.agent_max_tokens} is too high — keep ≤ 300"
    )


def test_agent_responsiveness_is_maximum():
    """responsiveness must be 1.0 to minimise turn-start delay."""
    assert settings.agent_responsiveness == 1.0, (
        f"agent_responsiveness={settings.agent_responsiveness} — must be 1.0"
    )


def test_agent_voice_model_is_turbo():
    """voice_model must be the turbo variant for lower TTS latency."""
    assert "turbo" in settings.agent_voice_model, (
        f"agent_voice_model='{settings.agent_voice_model}' — must be a turbo variant"
    )
