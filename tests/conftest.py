"""
tests/conftest.py

Shared fixtures for the Voice Agent Platform test suite.

Fixture hierarchy
─────────────────
redis_client   — fresh fakeredis async client per test (auto-flushed)
conv_state     — ConversationState wired to redis_client
"""
from __future__ import annotations

import pytest_asyncio
import fakeredis.aioredis as fake_aioredis

from app.services.conversation_state import ConversationState


# ---------------------------------------------------------------------------
# Redis — in-memory, async-compatible, redis.asyncio drop-in
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def redis_client():
    """
    Provides a fresh fakeredis async client per test.

    Uses a dedicated FakeServer so each test gets an isolated keyspace —
    no bleed between parallel or sequential tests.
    """
    import fakeredis
    server = fakeredis.FakeServer()
    r = fake_aioredis.FakeRedis(server=server, decode_responses=True)
    yield r
    await r.aclose()


# ---------------------------------------------------------------------------
# ConversationState wired to the isolated Redis fixture
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def conv_state(redis_client):
    """ConversationState backed by the per-test fakeredis instance."""
    return ConversationState(redis_client)
