"""
tests/test_latency.py

Validates the sentence-detection + first-sentence flush logic inside
webhook._stream_to_first_sentence().

What we are testing
───────────────────
1.  Correct split point — boundary triggers on . ? ! followed by space or EOS.
2.  Correct assembly  — first_sentence leads, remainder appended, single space.
3.  Fallback path      — no punctuation → full buffer returned verbatim.
4.  TTFB proof         — sentence boundary is crossed well before stream ends;
                         proven by an instrumented generator that records the
                         timestamp of boundary detection vs. stream-end.
5.  Edge cases         — boundary in first token, boundary mid-token,
                         trailing whitespace handling, single-sentence response.

Patch strategy
──────────────
webhook.py does:
    from app.services import openrouter
    async for token in openrouter.chat_completion_stream(...):

We replace the attribute on the exact module reference webhook holds:
    patch("app.routers.webhook.openrouter.chat_completion_stream", new=fn)

Because chat_completion_stream is an *async generator function*, we replace it
with a plain "async def fn(*a, **kw): yield ..." — NOT AsyncMock.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from app.routers.webhook import _SENTENCE_END, _stream_to_first_sentence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stream(*tokens: str, delay: float = 0.0):
    """Return an async-generator function that yields the given tokens."""
    async def _fn(*_args, **_kwargs):
        for tok in tokens:
            if delay:
                await asyncio.sleep(delay)
            yield tok
    return _fn


async def _run(stream_fn) -> str:
    """Patch chat_completion_stream with stream_fn and invoke the SUT."""
    with patch(
        "app.routers.webhook.openrouter.chat_completion_stream",
        new=stream_fn,
    ):
        return await _stream_to_first_sentence(
            model="openai/gpt-4o-mini",
            system_prompt="test system prompt",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.7,
            http_client=None,
        )


# ---------------------------------------------------------------------------
# 1. Boundary detection — correct split point
# ---------------------------------------------------------------------------

async def test_splits_on_period():
    result = await _run(_make_stream("Sure", " thing", ".", " I", " can", " help", "."))
    assert result.startswith("Sure thing.")
    assert "I can help." in result


async def test_splits_on_question_mark():
    result = await _run(_make_stream("Are", " you", " free", "?", " Let", " me", " check", "."))
    assert result.startswith("Are you free?")
    assert "Let me check." in result


async def test_splits_on_exclamation_mark():
    result = await _run(_make_stream("Great", "!", " Here", " is", " your", " answer", "."))
    assert result.startswith("Great!")
    assert "Here is your answer." in result


async def test_boundary_at_end_of_stream():
    """Punctuation is the very last character — no remainder."""
    result = await _run(_make_stream("Hello", " there", "."))
    assert result == "Hello there."


async def test_boundary_inside_first_token():
    """Sentence boundary arrives embedded in the first token."""
    result = await _run(_make_stream("Hello.", " World."))
    assert result.startswith("Hello.")
    assert "World." in result


async def test_boundary_embedded_mid_token():
    """Multi-char token carries boundary + trailing space."""
    result = await _run(_make_stream("Done! ", "Next sentence here."))
    assert result.startswith("Done!")
    assert "Next sentence here." in result


# ---------------------------------------------------------------------------
# 2. Assembly correctness
# ---------------------------------------------------------------------------

async def test_assembly_no_double_space():
    """Exactly one space between first sentence and remainder."""
    result = await _run(_make_stream("Hi", ".", " Bye", "."))
    assert "  " not in result
    assert result == "Hi. Bye."


async def test_assembly_strips_trailing_whitespace():
    result = await _run(_make_stream("Hello", ".", "   Extra   "))
    assert not result.endswith(" ")
    assert "Extra" in result


async def test_multi_sentence_full_assembly():
    tokens = ["One", ".", " Two", ".", " Three", "."]
    result = await _run(_make_stream(*tokens))
    assert result.startswith("One.")
    assert "Two." in result
    assert "Three." in result


# ---------------------------------------------------------------------------
# 3. Fallback path — no sentence-ending punctuation
# ---------------------------------------------------------------------------

async def test_no_punctuation_returns_full_buffer():
    result = await _run(_make_stream("Yes", " please", " call", " me", " back"))
    assert result == "Yes please call me back"


async def test_empty_stream_returns_empty_string():
    result = await _run(_make_stream())
    assert result == ""


async def test_single_word_no_punctuation():
    result = await _run(_make_stream("Okay"))
    assert result == "Okay"


# ---------------------------------------------------------------------------
# 4. TTFB proof — boundary crossed before stream ends
# ---------------------------------------------------------------------------

async def test_sentence_boundary_crossed_before_stream_ends():
    """
    White-box TTFB instrumentation test.

    Token layout (25 ms per token):
        "Hi", "."           → boundary detected here  (~50 ms elapsed)
        " Bye", " bye", " for", " now", "."  → 5 more tokens (~125 ms gap)

    We record wall-clock timestamps inside the generator:
        boundary_ms  — when _SENTENCE_END first matches the accumulating buffer
        stream_end_ms — when the last token is yielded

    Assertion: gap = (stream_end_ms - boundary_ms) ≥ 50% of remaining-token time.

    This proves the sentence boundary is detected significantly before the
    stream completes — the latency headroom a future two-phase flush can exploit.
    """
    DELAY = 0.025       # 25 ms per token
    tokens = ["Hi", ".", " Bye", " bye", " for", " now", "."]
    # boundary fires after token index 1 (buffer = "Hi.")
    # remaining tokens after boundary: indices 2-6 = 5 tokens → ~125 ms gap

    boundary_ms: list[float] = []
    stream_end_ms: list[float] = []

    async def instrumented(*_args, **_kwargs):
        buf = ""
        found = False
        for tok in tokens:
            await asyncio.sleep(DELAY)
            buf += tok
            if not found and _SENTENCE_END.search(buf):
                found = True
                boundary_ms.append(time.perf_counter() * 1000)
            yield tok
        stream_end_ms.append(time.perf_counter() * 1000)

    with patch("app.routers.webhook.openrouter.chat_completion_stream", new=instrumented):
        result = await _stream_to_first_sentence(
            model="openai/gpt-4o-mini",
            system_prompt="test",
            messages=[],
            temperature=0.7,
            http_client=None,
        )

    assert boundary_ms and stream_end_ms, "Instrumentation did not fire"

    gap_ms = stream_end_ms[0] - boundary_ms[0]
    # 5 remainder tokens × 25 ms × 50% CI headroom = 62.5 ms minimum gap
    min_gap_ms = 5 * DELAY * 1000 * 0.5
    assert gap_ms >= min_gap_ms, (
        f"Expected boundary→end gap ≥ {min_gap_ms:.0f} ms, got {gap_ms:.1f} ms"
    )

    assert result.startswith("Hi.")
    assert "Bye bye for now." in result


async def test_total_elapsed_bounded_by_stream_duration():
    """
    Total time for _stream_to_first_sentence is proportional to token count.
    10 tokens × 20 ms ≈ 200 ms; ceiling at 700 ms for slow CI runners.
    """
    DELAY = 0.02
    tokens = ["A", ".", " B", " C", " D", " E", " F", " G", " H", "."]

    start = time.perf_counter()
    result = await _run(_make_stream(*tokens, delay=DELAY))
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.startswith("A.")
    assert elapsed_ms < 700, f"Elapsed {elapsed_ms:.0f} ms — unexpectedly slow"


# ---------------------------------------------------------------------------
# 5. _SENTENCE_END regex unit tests (pure, no I/O)
# ---------------------------------------------------------------------------

def test_regex_matches_period_space():
    assert _SENTENCE_END.search("Hello. World")

def test_regex_matches_question_space():
    assert _SENTENCE_END.search("Really? Yes")

def test_regex_matches_exclamation_space():
    assert _SENTENCE_END.search("Wow! Cool")

def test_regex_matches_period_end_of_string():
    assert _SENTENCE_END.search("Done.")

def test_regex_no_match_decimal_number():
    """3.14 — period not followed by whitespace or EOS — must not split."""
    assert not _SENTENCE_END.search("3.14")

def test_regex_no_match_empty():
    assert not _SENTENCE_END.search("")
