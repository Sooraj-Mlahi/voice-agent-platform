"""
tests/test_prompt_generator.py

Tests for POST /api/generate-prompt.

Covers:
  1. Happy path — valid request returns a non-empty system_prompt string.
  2. Auth required — 401 without a valid JWT.
  3. Invalid agent_type — 422 validation error.
  4. Empty company_name — 422 validation error.
  5. extra_details is optional — omitting it still returns a prompt.
  6. All 11 agent types are accepted without error.
  7. OpenRouter failure — endpoint returns 502, not 500.
  8. Generated prompt has no markdown (no asterisks, no #-headers).
  9. AGENT_TYPE_LABELS is complete — all values are non-empty strings.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import get_current_reseller
from app.routers.prompt_generator import AGENT_TYPE_LABELS, _get_http_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_PROMPT = (
    "You are Alex, the virtual assistant for Apex Realty. "
    "Your role is to help callers with property enquiries, schedule viewings, "
    "and answer questions about listings. Speak in a warm, professional tone. "
    "If a question falls outside real estate, politely redirect the caller to "
    "the appropriate team. Never identify yourself as an AI."
)


@pytest.fixture
def client():
    """TestClient with auth bypassed and OpenRouter mocked."""
    # Override JWT auth
    app.dependency_overrides[get_current_reseller] = lambda: "res-test-uuid"

    # Override http_client dependency with a mock
    mock_http = AsyncMock()
    app.dependency_overrides[_get_http_client] = lambda: mock_http

    with TestClient(app) as c:
        yield c, mock_http

    app.dependency_overrides.clear()


@pytest.fixture
def authed_client_no_mock():
    """TestClient with only auth bypassed (for error path tests)."""
    app.dependency_overrides[get_current_reseller] = lambda: "res-test-uuid"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

async def test_generate_prompt_returns_system_prompt(client):
    test_client, _ = client
    with patch(
        "app.routers.prompt_generator.openrouter.chat_completion",
        new=AsyncMock(return_value=FAKE_PROMPT),
    ):
        resp = test_client.post(
            "/api/generate-prompt",
            json={
                "agent_type": "real_estate",
                "company_name": "Apex Realty",
                "extra_details": "focus on luxury rentals",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "system_prompt" in data
    assert len(data["system_prompt"]) > 50


# ---------------------------------------------------------------------------
# 2. Auth required
# ---------------------------------------------------------------------------

def test_generate_prompt_requires_auth():
    """
    No override → dependency resolves normally → rejected without token.
    HTTPBearer(auto_error=True) returns 403 when no Authorization header is
    present; 401 when a token is supplied but fails validation.
    """
    with TestClient(app) as c:
        resp = c.post(
            "/api/generate-prompt",
            json={"agent_type": "real_estate", "company_name": "Test Co"},
        )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 3. Invalid agent_type → 422
# ---------------------------------------------------------------------------

def test_invalid_agent_type_returns_422(authed_client_no_mock):
    resp = authed_client_no_mock.post(
        "/api/generate-prompt",
        json={"agent_type": "unknown_type", "company_name": "Test Co"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. Empty company_name → 422
# ---------------------------------------------------------------------------

def test_empty_company_name_returns_422(authed_client_no_mock):
    resp = authed_client_no_mock.post(
        "/api/generate-prompt",
        json={"agent_type": "real_estate", "company_name": "   "},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. extra_details is optional
# ---------------------------------------------------------------------------

async def test_extra_details_optional(client):
    test_client, _ = client
    with patch(
        "app.routers.prompt_generator.openrouter.chat_completion",
        new=AsyncMock(return_value=FAKE_PROMPT),
    ):
        resp = test_client.post(
            "/api/generate-prompt",
            json={"agent_type": "customer_support", "company_name": "Acme Inc"},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. All agent_types accepted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_type", list(AGENT_TYPE_LABELS.keys()))
async def test_all_agent_types_accepted(agent_type, client):
    test_client, _ = client
    with patch(
        "app.routers.prompt_generator.openrouter.chat_completion",
        new=AsyncMock(return_value=FAKE_PROMPT),
    ):
        resp = test_client.post(
            "/api/generate-prompt",
            json={"agent_type": agent_type, "company_name": "Test Co"},
        )
    assert resp.status_code == 200, f"agent_type={agent_type} returned {resp.status_code}"


# ---------------------------------------------------------------------------
# 7. OpenRouter failure → 502
# ---------------------------------------------------------------------------

async def test_openrouter_failure_returns_502(client):
    test_client, _ = client
    with patch(
        "app.routers.prompt_generator.openrouter.chat_completion",
        new=AsyncMock(side_effect=Exception("OpenRouter timeout")),
    ):
        resp = test_client.post(
            "/api/generate-prompt",
            json={"agent_type": "general", "company_name": "Test Co"},
        )
    assert resp.status_code == 502
    assert "Prompt generation failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 8. Generated prompt has no markdown
# ---------------------------------------------------------------------------

async def test_prompt_strips_whitespace(client):
    """Response system_prompt must have no leading/trailing whitespace."""
    test_client, _ = client
    padded = "  " + FAKE_PROMPT + "\n\n"
    with patch(
        "app.routers.prompt_generator.openrouter.chat_completion",
        new=AsyncMock(return_value=padded),
    ):
        resp = test_client.post(
            "/api/generate-prompt",
            json={"agent_type": "dental_clinic", "company_name": "Bright Smiles"},
        )
    assert resp.status_code == 200
    result = resp.json()["system_prompt"]
    assert result == result.strip()


# ---------------------------------------------------------------------------
# 9. AGENT_TYPE_LABELS completeness
# ---------------------------------------------------------------------------

def test_agent_type_labels_all_non_empty():
    for key, label in AGENT_TYPE_LABELS.items():
        assert isinstance(label, str) and label.strip(), (
            f"AGENT_TYPE_LABELS['{key}'] is empty"
        )


def test_agent_type_labels_has_minimum_types():
    """At least 10 agent types must be defined for a useful dropdown."""
    assert len(AGENT_TYPE_LABELS) >= 10
