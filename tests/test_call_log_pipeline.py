"""
tests/test_call_log_pipeline.py

Tests for the call log save + read pipeline:

  1. _log_call_to_supabase  — inserts a new call, updates an existing one
  2. _fetch_agent_config    — two-query customer lookup
  3. retell_webhook         — full call_analyzed end-to-end (no DB, no Retell)
  4. list_calls             — two-query reseller-scoped read
  5. Latency field          — latency_p50_ms extracted correctly from e2e_latency

All Supabase calls are intercepted with unittest.mock — no real DB connections.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import get_current_reseller
from app.main import app
from app.routers.webhook import (
    _log_call_to_supabase,
    _fetch_agent_config,
)


# ---------------------------------------------------------------------------
# Helpers — build realistic Retell call_analyzed payloads
# ---------------------------------------------------------------------------

def _make_call(
    call_id: str = "call_test123",
    agent_id: str = "agent_abc",
    transcript: list | None = None,
    duration_ms: int = 60_000,
    combined_cost: int | None = 500,  # top-level cents (v1) or None
    e2e_latency: dict | None = None,
    call_cost: dict | None = None,
    disconnection_reason: str = "user_hangup",
) -> dict[str, Any]:
    if transcript is None:
        transcript = [
            {"role": "agent", "content": "Hello, how can I help?"},
            {"role": "user", "content": "I need an appointment."},
            {"role": "agent", "content": "Sure, let me check availability."},
        ]
    if e2e_latency is None:
        e2e_latency = {"p50": 420, "p90": 810}
    if call_cost is None:
        call_cost = {"llm_tokens_used": 312, "llm_cost": 0.0004}

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return {
        "call_id": call_id,
        "agent_id": agent_id,
        "transcript": transcript,
        "duration_ms": duration_ms,
        "combined_cost": combined_cost,
        "e2e_latency": e2e_latency,
        "call_cost": call_cost,
        "disconnection_reason": disconnection_reason,
        "start_timestamp": now_ms - duration_ms,
        "end_timestamp": now_ms,
        "from_number": "+15550001234",
    }


def _make_db_mock(
    customer_row: dict | None = None,
    agent_config_row: dict | None = None,
    existing_call_row: dict | None = None,
) -> MagicMock:
    """
    Returns a fully-stubbed Supabase client mock.
    Each table().select().eq()...execute() chain returns the configured data.
    """
    db = MagicMock()

    def _chain(data):
        """Build a fluent mock chain that always terminates with .execute() → data."""
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.in_.return_value = m
        m.limit.return_value = m
        m.single.return_value = m
        m.update.return_value = m
        m.insert.return_value = m
        m.order.return_value = m
        m.execute.return_value = MagicMock(data=data)
        return m

    def table_router(name: str):
        if name == "customers":
            return _chain(customer_row)
        if name == "agent_configs":
            return _chain([agent_config_row] if agent_config_row else [])
        if name == "calls":
            # select("id") for existence check returns existing_call_row
            # insert/update chains also need to work
            m = _chain([existing_call_row] if existing_call_row else [])
            return m
        return _chain(None)

    db.table.side_effect = table_router
    return db


# ---------------------------------------------------------------------------
# 1. _log_call_to_supabase — INSERT path (new call)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_call_inserts_new_call():
    """When no existing call with that retell_call_id, an INSERT is performed."""
    call = _make_call(call_id="call_new001")

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[{"id": "uuid-1"}])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])  # no existing row

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock
    calls_table.insert.return_value = insert_mock

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "customer-uuid-123", "warm-conversational")

    # insert() must have been called (not update)
    calls_table.insert.assert_called_once()
    calls_table.update.assert_not_called()


@pytest.mark.asyncio
async def test_log_call_updates_existing_call():
    """When a call with the same retell_call_id already exists, UPDATE is used."""
    call = _make_call(call_id="call_dup001")

    update_mock = MagicMock()
    update_mock.eq.return_value = update_mock
    update_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[{"id": "existing-uuid"}])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock
    calls_table.update.return_value = update_mock

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "customer-uuid-123", "warm-conversational")

    calls_table.update.assert_called_once()
    calls_table.insert.assert_not_called()


# ---------------------------------------------------------------------------
# 2. _log_call_to_supabase — field extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_call_extracts_latency_p50():
    """latency_p50_ms is taken from e2e_latency.p50."""
    call = _make_call(e2e_latency={"p50": 380, "p90": 750})
    captured_record: dict = {}

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock

    def capture_insert(record):
        captured_record.update(record)
        return insert_mock

    calls_table.insert.side_effect = capture_insert

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "cust-id", "warm-conversational")

    assert captured_record.get("latency_p50_ms") == 380, (
        f"Expected latency_p50_ms=380, got {captured_record.get('latency_p50_ms')}"
    )


@pytest.mark.asyncio
async def test_log_call_extracts_cost_usd_from_call_cost_v2():
    """Retell v2: combined_cost is inside call_cost dict, already in USD."""
    # Remove top-level combined_cost; put it inside call_cost
    call = _make_call(combined_cost=None, call_cost={"combined_cost": 0.05, "total_tokens": 300})
    captured_record: dict = {}

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock

    def capture_insert(record):
        captured_record.update(record)
        return insert_mock

    calls_table.insert.side_effect = capture_insert

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "cust-id", "warm-conversational")

    assert captured_record.get("cost_usd") == pytest.approx(0.05), (
        f"v2 cost_usd expected 0.05, got {captured_record.get('cost_usd')}"
    )


@pytest.mark.asyncio
async def test_log_call_extracts_cost_usd():
    """Retell v1 fallback: top-level combined_cost in cents → divide by 100."""
    call = _make_call(combined_cost=1234, call_cost={})   # 1234 cents = $12.34
    captured_record: dict = {}

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock

    def capture_insert(record):
        captured_record.update(record)
        return insert_mock

    calls_table.insert.side_effect = capture_insert

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "cust-id", "warm-conversational")

    assert captured_record.get("cost_usd") == pytest.approx(12.34), (
        f"Expected cost_usd≈12.34, got {captured_record.get('cost_usd')}"
    )


@pytest.mark.asyncio
async def test_log_call_extracts_tokens_used():
    """tokens_used comes from call_cost.llm_tokens_used."""
    call = _make_call(call_cost={"llm_tokens_used": 512, "llm_cost": 0.001})
    captured_record: dict = {}

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock

    def capture_insert(record):
        captured_record.update(record)
        return insert_mock

    calls_table.insert.side_effect = capture_insert

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "cust-id", "warm-conversational")

    assert captured_record.get("tokens_used") == 512


@pytest.mark.asyncio
async def test_log_call_serialises_transcript_to_json_string():
    """Transcript list must be stored as a JSON string, not a raw list."""
    turns = [
        {"role": "agent", "content": "Hi there!"},
        {"role": "user", "content": "Hello."},
    ]
    call = _make_call(transcript=turns)
    captured_record: dict = {}

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock

    def capture_insert(record):
        captured_record.update(record)
        return insert_mock

    calls_table.insert.side_effect = capture_insert

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "cust-id", "warm-conversational")

    stored = captured_record.get("transcript")
    assert isinstance(stored, str), "transcript must be stored as a JSON string"
    parsed = json.loads(stored)
    assert parsed == turns, "Round-tripped transcript does not match original"


@pytest.mark.asyncio
async def test_log_call_stores_prosody_style():
    """prosody_style_used is taken from the parameter, not from the call payload."""
    call = _make_call()
    captured_record: dict = {}

    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[])

    select_mock = MagicMock()
    select_mock.eq.return_value = select_mock
    select_mock.limit.return_value = select_mock
    select_mock.execute.return_value = MagicMock(data=[])

    calls_table = MagicMock()
    calls_table.select.return_value = select_mock

    def capture_insert(record):
        captured_record.update(record)
        return insert_mock

    calls_table.insert.side_effect = capture_insert

    db = MagicMock()
    db.table.return_value = calls_table

    with patch("app.routers.webhook.get_supabase", return_value=db):
        await _log_call_to_supabase(call, "cust-id", "empathetic")

    assert captured_record.get("prosody_style_used") == "empathetic"


# ---------------------------------------------------------------------------
# 3. _fetch_agent_config — two-query lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_agent_config_returns_customer_and_config():
    """Happy path: customer found, agent_config returned."""
    customer_row = {"id": "cust-uuid", "reseller_id": "res-uuid"}
    config_row = {
        "id": "cfg-uuid",
        "customer_id": "cust-uuid",
        "system_prompt": "You are helpful.",
        "prosody_style": "formal",
        "llm_model": "gpt-4o-mini",
    }

    # Mock customers table
    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.single.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(data=customer_row)

    # Mock agent_configs table
    configs_chain = MagicMock()
    configs_chain.select.return_value = configs_chain
    configs_chain.eq.return_value = configs_chain
    configs_chain.limit.return_value = configs_chain
    configs_chain.execute.return_value = MagicMock(data=[config_row])

    db = MagicMock()
    db.table.side_effect = lambda name: customers_chain if name == "customers" else configs_chain

    with patch("app.routers.webhook.get_supabase", return_value=db):
        result = await _fetch_agent_config("agent_abc")

    assert result["customer_id"] == "cust-uuid"
    assert result["reseller_id"] == "res-uuid"
    assert result["agent_config"]["prosody_style"] == "formal"


@pytest.mark.asyncio
async def test_fetch_agent_config_raises_404_when_no_customer():
    """If no customer has this retell_agent_id, HTTPException 404 is raised."""
    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.single.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(data=None)  # not found

    db = MagicMock()
    db.table.return_value = customers_chain

    with patch("app.routers.webhook.get_supabase", return_value=db):
        with pytest.raises(HTTPException) as exc_info:
            await _fetch_agent_config("agent_does_not_exist")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_fetch_agent_config_defaults_when_no_agent_config():
    """If agent_configs is empty for the customer, agent_config defaults to {}."""
    customer_row = {"id": "cust-uuid", "reseller_id": "res-uuid"}

    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.single.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(data=customer_row)

    configs_chain = MagicMock()
    configs_chain.select.return_value = configs_chain
    configs_chain.eq.return_value = configs_chain
    configs_chain.limit.return_value = configs_chain
    configs_chain.execute.return_value = MagicMock(data=[])  # empty

    db = MagicMock()
    db.table.side_effect = lambda name: customers_chain if name == "customers" else configs_chain

    with patch("app.routers.webhook.get_supabase", return_value=db):
        result = await _fetch_agent_config("agent_abc")

    assert result["customer_id"] == "cust-uuid"
    assert result["agent_config"] == {}


# ---------------------------------------------------------------------------
# 4. call_analyzed end-to-end via TestClient
# ---------------------------------------------------------------------------

def _make_analyzed_payload(agent_id: str = "agent_abc") -> dict:
    call = _make_call(agent_id=agent_id)
    return {"event": "call_analyzed", "call": call}


def _mock_db_for_analyzed(customer_id: str = "cust-uuid") -> MagicMock:
    """
    Returns a DB mock that:
      - customers.select() → customer row
      - agent_configs.select() → one config row
      - calls.select() (existence check) → empty (new call)
      - calls.insert() → success
    """
    customer_row = {"id": customer_id, "reseller_id": "res-uuid"}
    config_row = {
        "id": "cfg-uuid",
        "customer_id": customer_id,
        "system_prompt": "Help with appointments.",
        "prosody_style": "warm-conversational",
        "llm_model": "gpt-4o-mini",
        "temperature": 0.7,
    }

    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.single.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(data=customer_row)

    configs_chain = MagicMock()
    configs_chain.select.return_value = configs_chain
    configs_chain.eq.return_value = configs_chain
    configs_chain.limit.return_value = configs_chain
    configs_chain.execute.return_value = MagicMock(data=[config_row])

    calls_select_chain = MagicMock()
    calls_select_chain.eq.return_value = calls_select_chain
    calls_select_chain.limit.return_value = calls_select_chain
    calls_select_chain.execute.return_value = MagicMock(data=[])   # new call

    calls_insert_chain = MagicMock()
    calls_insert_chain.execute.return_value = MagicMock(data=[{"id": "new-call-uuid"}])

    calls_table = MagicMock()
    calls_table.select.return_value = calls_select_chain
    calls_table.insert.return_value = calls_insert_chain

    db = MagicMock()

    def route(name: str):
        if name == "customers":
            return customers_chain
        if name == "agent_configs":
            return configs_chain
        if name == "calls":
            return calls_table
        return MagicMock()

    db.table.side_effect = route
    return db


def test_call_analyzed_returns_logged():
    """POST /webhook/retell with call_analyzed must return {"status": "logged"}."""
    db = _mock_db_for_analyzed()

    with patch("app.routers.webhook.get_supabase", return_value=db):
        client = TestClient(app)
        resp = client.post("/webhook/retell", json=_make_analyzed_payload())

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json() == {"status": "logged"}


def test_call_analyzed_triggers_db_insert():
    """call_analyzed must call db.table('calls').insert() exactly once."""
    db = _mock_db_for_analyzed()

    calls_table = db.table.side_effect("calls")

    with patch("app.routers.webhook.get_supabase", return_value=db):
        client = TestClient(app)
        client.post("/webhook/retell", json=_make_analyzed_payload())

    # calls_table.insert must have been called (the new-call path)
    calls_table.insert.assert_called_once()


def test_call_analyzed_unknown_agent_returns_404():
    """If agent_id matches no customer, webhook must return 404."""
    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.single.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(data=None)  # not found

    db = MagicMock()
    db.table.return_value = customers_chain

    with patch("app.routers.webhook.get_supabase", return_value=db):
        client = TestClient(app)
        resp = client.post(
            "/webhook/retell",
            json={"event": "call_analyzed", "call": {"agent_id": "agent_unknown", "call_id": "x"}},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. list_calls — two-query reseller-scoped read
# ---------------------------------------------------------------------------

def _make_calls_db_mock(
    customer_ids: list[str],
    call_rows: list[dict],
) -> MagicMock:
    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(
        data=[{"id": cid} for cid in customer_ids]
    )

    calls_chain = MagicMock()
    calls_chain.select.return_value = calls_chain
    calls_chain.in_.return_value = calls_chain
    calls_chain.order.return_value = calls_chain
    calls_chain.execute.return_value = MagicMock(data=call_rows)

    db = MagicMock()

    def route(name: str):
        if name == "customers":
            return customers_chain
        if name == "calls":
            return calls_chain
        return MagicMock()

    db.table.side_effect = route
    return db


def test_list_calls_returns_calls_for_reseller():
    """GET /api/calls must return only calls whose customer belongs to the reseller."""
    call_rows = [
        {"id": "c1", "customer_id": "cust-1", "retell_call_id": "call_001",
         "duration_seconds": 60, "outcome": "completed", "transcript": "[]",
         "started_at": "2026-03-21T00:00:00+00:00", "ended_at": None,
         "cost_usd": 0.05, "latency_p50_ms": 420, "tokens_used": 300,
         "prosody_style_used": "warm-conversational"},
    ]
    db = _make_calls_db_mock(["cust-1", "cust-2"], call_rows)

    app.dependency_overrides[get_current_reseller] = lambda: "res-uuid"
    try:
        with patch("app.routers.calls.get_supabase", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/calls")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["retell_call_id"] == "call_001"


def test_list_calls_returns_empty_when_no_customers():
    """If the reseller has no customers, return [] without querying calls."""
    customers_chain = MagicMock()
    customers_chain.select.return_value = customers_chain
    customers_chain.eq.return_value = customers_chain
    customers_chain.execute.return_value = MagicMock(data=[])

    calls_chain = MagicMock()

    db = MagicMock()
    db.table.side_effect = lambda name: customers_chain if name == "customers" else calls_chain

    app.dependency_overrides[get_current_reseller] = lambda: "res-uuid"
    try:
        with patch("app.routers.calls.get_supabase", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/calls")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []
    calls_chain.select.assert_not_called()


def test_list_calls_latency_field_present():
    """latency_p50_ms must be present in the response when stored in DB."""
    call_rows = [
        {"id": "c1", "customer_id": "cust-1", "retell_call_id": "call_lat",
         "duration_seconds": 45, "outcome": "completed", "transcript": "[]",
         "started_at": "2026-03-26T00:00:00+00:00", "ended_at": None,
         "cost_usd": 0.02, "latency_p50_ms": 380, "tokens_used": 200,
         "prosody_style_used": "formal"},
    ]
    db = _make_calls_db_mock(["cust-1"], call_rows)

    app.dependency_overrides[get_current_reseller] = lambda: "res-uuid"
    try:
        with patch("app.routers.calls.get_supabase", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/calls")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["latency_p50_ms"] == 380, (
        f"latency_p50_ms not returned to frontend: {data[0]}"
    )
    assert data[0]["cost_usd"] == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# 6. Lifecycle events — call_started and call_ended return ok without DB calls
# ---------------------------------------------------------------------------

def test_call_started_returns_ok_no_db():
    """call_started must return {status: ok} and never touch the DB."""
    db = MagicMock()

    with patch("app.routers.webhook.get_supabase", return_value=db):
        client = TestClient(app)
        resp = client.post(
            "/webhook/retell",
            json={"event": "call_started", "call": {"agent_id": "x", "call_id": "y"}},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    db.table.assert_not_called()


def test_call_ended_returns_ok_no_db():
    """call_ended must return {status: ok} — DB logging is deferred to call_analyzed."""
    db = MagicMock()

    with patch("app.routers.webhook.get_supabase", return_value=db):
        client = TestClient(app)
        resp = client.post(
            "/webhook/retell",
            json={"event": "call_ended", "call": {"agent_id": "x", "call_id": "y"}},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    db.table.assert_not_called()
