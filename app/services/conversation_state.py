"""
ConversationState — Redis-backed call state tracker.

Tracks per-call state across multiple FastAPI worker processes using Redis.
Each call_id maps to a hash with:
  - silence_prompt_count : int   — how many silence reminders have been sent
  - topic_locked         : "1"   — always True (guardrails always active)

Keys expire automatically (TTL = 3 hours) to avoid stale data accumulation.

Usage (in webhook.py):
    from app.services.conversation_state import ConversationState
    state = ConversationState(redis_client)
    count = await state.increment_silence(call_id)
    await state.record_user_turn(call_id)
    await state.clear(call_id)
"""
from __future__ import annotations

import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "voice_agent:call:"
_TTL_SECONDS = 10_800  # 3 hours — well beyond any real call duration


def _key(call_id: str) -> str:
    return f"{_KEY_PREFIX}{call_id}"


class ConversationState:
    """
    Thin async wrapper around a Redis hash per call_id.

    Instantiate once per request with the Redis client from app.state:
        state = ConversationState(request.app.state.redis)
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    # ------------------------------------------------------------------ #
    # Silence counter
    # ------------------------------------------------------------------ #

    async def increment_silence(self, call_id: str) -> int:
        """
        Atomically increment the silence counter for this call.
        Returns the new count (1 → first reminder, 2 → second reminder,
        3+ → hang-up signal).
        Initialises the key with a TTL if it doesn't exist yet.
        """
        key = _key(call_id)
        # Use a pipeline: HINCRBY + EXPIRE in one round-trip
        pipe = self._r.pipeline()
        pipe.hincrby(key, "silence_prompt_count", 1)
        pipe.expire(key, _TTL_SECONDS)
        results = await pipe.execute()
        count: int = results[0]
        logger.info("Silence count for call %s → %d", call_id, count)
        return count

    async def get_silence_count(self, call_id: str) -> int:
        """Return the current silence count without incrementing."""
        raw = await self._r.hget(_key(call_id), "silence_prompt_count")
        return int(raw) if raw else 0

    # ------------------------------------------------------------------ #
    # User-turn reset
    # ------------------------------------------------------------------ #

    async def record_user_turn(self, call_id: str) -> None:
        """
        Reset the silence counter when the user speaks.
        Called whenever a non-silence LLM event carries user transcript content.
        """
        key = _key(call_id)
        pipe = self._r.pipeline()
        pipe.hset(key, "silence_prompt_count", 0)
        pipe.expire(key, _TTL_SECONDS)
        await pipe.execute()
        logger.debug("Silence counter reset for call %s (user spoke).", call_id)

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #

    async def clear(self, call_id: str) -> None:
        """
        Delete the state key for a call.
        Called on call_ended / call_analyzed events.
        """
        await self._r.delete(_key(call_id))
        logger.debug("Conversation state cleared for call %s.", call_id)
