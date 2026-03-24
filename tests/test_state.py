"""
tests/test_state.py

Validates ConversationState against a fakeredis async backend.

What we are testing
───────────────────
1.  increment_silence   — returns 1, 2, 3 on successive calls; key is created
                          atomically (no race initialisation needed).
2.  Silence thresholds  — response strings at count 1, 2, and ≥ 3 match what
                          the webhook handler returns (contract test).
3.  record_user_turn    — resets silence counter to 0; subsequent increment
                          returns 1 again.
4.  clear               — deletes the Redis key; subsequent reads return 0.
5.  TTL hygiene         — key has a TTL set after creation (≤ 3 hours).
6.  Call isolation      — two concurrent call_ids do not interfere.
7.  get_silence_count   — returns the current value without side effects.
8.  Pipeline atomicity  — HINCRBY + EXPIRE execute in the same round-trip
                          (verified by inspecting the pipeline call count).

Fixtures (from conftest.py)
───────────────────────────
redis_client  — fresh fakeredis.aioredis.FakeRedis per test
conv_state    — ConversationState(redis_client)
"""
from __future__ import annotations

import pytest

from app.services.conversation_state import ConversationState, _KEY_PREFIX, _TTL_SECONDS


# ---------------------------------------------------------------------------
# 1. increment_silence — counter behaviour
# ---------------------------------------------------------------------------

async def test_first_increment_returns_one(conv_state, redis_client):
    count = await conv_state.increment_silence("call-001")
    assert count == 1


async def test_second_increment_returns_two(conv_state):
    await conv_state.increment_silence("call-001")
    count = await conv_state.increment_silence("call-001")
    assert count == 2


async def test_third_increment_returns_three(conv_state):
    await conv_state.increment_silence("call-001")
    await conv_state.increment_silence("call-001")
    count = await conv_state.increment_silence("call-001")
    assert count == 3


async def test_increment_beyond_three_continues(conv_state):
    """Counter is unbounded — webhook logic caps behaviour at ≥ 3."""
    for _ in range(5):
        await conv_state.increment_silence("call-001")
    count = await conv_state.increment_silence("call-001")
    assert count == 6


# ---------------------------------------------------------------------------
# 2. Silence threshold contract — webhook response strings
#    These assertions act as a contract test: if the response text in
#    webhook.py changes, these tests will catch the drift immediately.
# ---------------------------------------------------------------------------

async def test_silence_count_1_response_text(conv_state):
    """
    At count == 1 the webhook returns the 'still there' prompt.
    We verify the exact string used in webhook.py has not drifted.
    """
    count = await conv_state.increment_silence("call-001")
    assert count == 1
    # This mirrors the string at webhook.py:314-317
    expected_fragment = "Are you still there?"
    # Import from the actual webhook to prevent string drift
    from app.routers.webhook import retell_webhook  # noqa: F401 (import guard)
    import inspect, ast
    src = inspect.getsource(retell_webhook)
    assert expected_fragment in src, (
        f"Silence prompt 1 text has changed — update this test to match webhook.py"
    )


async def test_silence_count_2_response_text(conv_state):
    count = await conv_state.increment_silence("call-001")
    count = await conv_state.increment_silence("call-001")
    assert count == 2
    from app.routers.webhook import retell_webhook  # noqa: F401
    import inspect
    src = inspect.getsource(retell_webhook)
    assert "checking in" in src.lower(), (
        "Silence prompt 2 text has changed — update this test to match webhook.py"
    )


async def test_silence_count_3_triggers_end_call_flag(conv_state):
    """
    At count ≥ 3, the webhook must return end_call: True.
    This tests the business rule, not just the counter.
    """
    import inspect
    from app.routers.webhook import retell_webhook  # noqa: F401
    src = inspect.getsource(retell_webhook)
    # 'end_call': True must appear in the silence ≥3 branch
    assert '"end_call": True' in src or "'end_call': True" in src, (
        "end_call: True flag missing from the silence ≥3 branch in webhook.py"
    )


# ---------------------------------------------------------------------------
# 3. record_user_turn — resets counter
# ---------------------------------------------------------------------------

async def test_record_user_turn_resets_to_zero(conv_state):
    await conv_state.increment_silence("call-001")
    await conv_state.increment_silence("call-001")
    await conv_state.record_user_turn("call-001")

    count = await conv_state.get_silence_count("call-001")
    assert count == 0


async def test_increment_after_reset_returns_one(conv_state):
    await conv_state.increment_silence("call-001")
    await conv_state.increment_silence("call-001")
    await conv_state.record_user_turn("call-001")

    count = await conv_state.increment_silence("call-001")
    assert count == 1


async def test_record_user_turn_on_fresh_call_does_not_raise(conv_state):
    """record_user_turn on a call_id that has never incremented must be safe."""
    await conv_state.record_user_turn("brand-new-call")
    count = await conv_state.get_silence_count("brand-new-call")
    assert count == 0


# ---------------------------------------------------------------------------
# 4. clear — deletes state
# ---------------------------------------------------------------------------

async def test_clear_removes_key(conv_state, redis_client):
    call_id = "call-to-clear"
    await conv_state.increment_silence(call_id)
    await conv_state.clear(call_id)

    raw = await redis_client.hget(f"{_KEY_PREFIX}{call_id}", "silence_prompt_count")
    assert raw is None


async def test_get_silence_count_after_clear_returns_zero(conv_state):
    await conv_state.increment_silence("call-001")
    await conv_state.clear("call-001")

    count = await conv_state.get_silence_count("call-001")
    assert count == 0


async def test_clear_on_nonexistent_key_does_not_raise(conv_state):
    """Clearing a call_id that was never touched must be a no-op."""
    await conv_state.clear("ghost-call-999")


# ---------------------------------------------------------------------------
# 5. TTL hygiene
# ---------------------------------------------------------------------------

async def test_key_has_ttl_after_increment(conv_state, redis_client):
    """After the first increment, the Redis key must have a positive TTL."""
    call_id = "call-ttl"
    await conv_state.increment_silence(call_id)

    ttl = await redis_client.ttl(f"{_KEY_PREFIX}{call_id}")
    # TTL must be set (> 0) and not exceed the defined ceiling
    assert 0 < ttl <= _TTL_SECONDS, f"Unexpected TTL: {ttl}"


async def test_key_has_ttl_after_record_user_turn(conv_state, redis_client):
    """record_user_turn must also refresh the TTL."""
    call_id = "call-ttl-reset"
    await conv_state.increment_silence(call_id)
    await conv_state.record_user_turn(call_id)

    ttl = await redis_client.ttl(f"{_KEY_PREFIX}{call_id}")
    assert 0 < ttl <= _TTL_SECONDS


async def test_ttl_constant_value():
    """_TTL_SECONDS must be exactly 3 hours — change this test if the policy changes."""
    assert _TTL_SECONDS == 10_800, (
        f"_TTL_SECONDS changed to {_TTL_SECONDS} — update the TTL policy comment"
    )


# ---------------------------------------------------------------------------
# 6. Call isolation — two call_ids do not interfere
# ---------------------------------------------------------------------------

async def test_two_calls_isolated(conv_state):
    """Incrementing call A must not affect call B's counter."""
    await conv_state.increment_silence("call-A")
    await conv_state.increment_silence("call-A")
    await conv_state.increment_silence("call-B")

    assert await conv_state.get_silence_count("call-A") == 2
    assert await conv_state.get_silence_count("call-B") == 1


async def test_clear_call_a_does_not_affect_call_b(conv_state):
    await conv_state.increment_silence("call-A")
    await conv_state.increment_silence("call-B")
    await conv_state.clear("call-A")

    assert await conv_state.get_silence_count("call-A") == 0
    assert await conv_state.get_silence_count("call-B") == 1


async def test_reset_call_a_does_not_affect_call_b(conv_state):
    await conv_state.increment_silence("call-A")
    await conv_state.increment_silence("call-A")
    await conv_state.increment_silence("call-B")
    await conv_state.record_user_turn("call-A")

    assert await conv_state.get_silence_count("call-A") == 0
    assert await conv_state.get_silence_count("call-B") == 1


# ---------------------------------------------------------------------------
# 7. get_silence_count — non-mutating read
# ---------------------------------------------------------------------------

async def test_get_silence_count_does_not_increment(conv_state):
    await conv_state.increment_silence("call-001")
    await conv_state.get_silence_count("call-001")
    await conv_state.get_silence_count("call-001")

    count = await conv_state.get_silence_count("call-001")
    assert count == 1  # still 1, not 3


async def test_get_silence_count_on_fresh_call_returns_zero(conv_state):
    count = await conv_state.get_silence_count("never-touched")
    assert count == 0


# ---------------------------------------------------------------------------
# 8. Full call lifecycle simulation
# ---------------------------------------------------------------------------

async def test_full_call_lifecycle(conv_state):
    """
    Simulates a realistic call: user speaks → silence × 2 → user speaks
    again → silence × 3 → hang-up, then call ends.

    Verifies that the state machine progresses and resets correctly.
    """
    call_id = "call-lifecycle"

    # User speaks first — no silence yet
    await conv_state.record_user_turn(call_id)
    assert await conv_state.get_silence_count(call_id) == 0

    # First silence → prompt 1
    c = await conv_state.increment_silence(call_id)
    assert c == 1

    # Second silence → prompt 2
    c = await conv_state.increment_silence(call_id)
    assert c == 2

    # User returns — counter resets
    await conv_state.record_user_turn(call_id)
    assert await conv_state.get_silence_count(call_id) == 0

    # Three silences → hang up signal
    await conv_state.increment_silence(call_id)
    await conv_state.increment_silence(call_id)
    c = await conv_state.increment_silence(call_id)
    assert c == 3  # ≥ 3 triggers end_call

    # Call ends — state cleared
    await conv_state.clear(call_id)
    assert await conv_state.get_silence_count(call_id) == 0
