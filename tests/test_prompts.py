"""
tests/test_prompts.py

Validates PromptBuilder across all prosody_style presets.

What we are testing
───────────────────
1.  Structure       — every output contains header, optional business block,
                      and footer in the correct order.
2.  Style injection — each of the four named presets injects the correct
                      tone line; unknown styles fall back to default.
3.  Filler rules    — the anti-filler rule ("AT MOST ONE") is present in
                      every style variant — it must never be dropped.
4.  Guardrail rules — the behavioral footer's key rules are always present.
5.  Empty prompt    — build_prompt("") must not raise and must still contain
                      header + footer (no empty "Business Instructions" block).
6.  Idempotency     — calling build_prompt twice on the same input is pure
                      (no accumulated state, no stacked headers).
7.  Stacking guard  — if build_prompt is called on an already-wrapped prompt
                      (e.g. someone passes the output back in), the header
                      must NOT appear twice (regression guard).
8.  Separator       — sections are separated by double newlines, not single.
9.  Anti-robot      — "Certainly!" and "Great question!" bans are in every output.
"""
from __future__ import annotations

import pytest

from app.services.prompt_builder import (
    build_prompt,
    _TONE_LINES,
    _DEFAULT_STYLE,
    _PROSODY_HEADER,
    _BEHAVIORAL_FOOTER,
)

# ---------------------------------------------------------------------------
# Constants pulled from the module so tests stay in sync automatically
# ---------------------------------------------------------------------------
ALL_STYLES = list(_TONE_LINES.keys())
RAW_PROMPT = "You are the virtual assistant for Sunshine Dental. Help patients book appointments."


# ---------------------------------------------------------------------------
# 1. Structure — sections appear in the right order
# ---------------------------------------------------------------------------

def test_header_before_business_instructions():
    result = build_prompt(RAW_PROMPT)
    header_pos = result.find("Voice Delivery Instructions")
    business_pos = result.find("Business Instructions")
    assert header_pos < business_pos, "Header must precede Business Instructions"


def test_business_instructions_before_footer():
    result = build_prompt(RAW_PROMPT)
    business_pos = result.find("Business Instructions")
    footer_pos = result.find("Strict Operating Rules")
    assert business_pos < footer_pos, "Business Instructions must precede footer"


def test_three_sections_separated_by_double_newline():
    result = build_prompt(RAW_PROMPT)
    # Each section boundary uses '\n\n' as the join separator
    assert "\n\n" in result
    # There must be at least 2 double-newline separators (3 sections → 2 gaps)
    assert result.count("\n\n") >= 2


def test_raw_prompt_content_preserved():
    result = build_prompt(RAW_PROMPT)
    assert "Sunshine Dental" in result
    assert "book appointments" in result


# ---------------------------------------------------------------------------
# 2. Style injection — correct tone line per preset
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ALL_STYLES)
def test_correct_tone_line_injected(style: str):
    """Each style injects its own unique tone line into the header."""
    result = build_prompt(RAW_PROMPT, style=style)
    expected_fragment = _TONE_LINES[style][:40]  # first 40 chars are unique enough
    assert expected_fragment in result, (
        f"Tone line for style '{style}' not found in output"
    )


def test_unknown_style_falls_back_to_default():
    result_unknown = build_prompt(RAW_PROMPT, style="does-not-exist")
    result_default = build_prompt(RAW_PROMPT, style=_DEFAULT_STYLE)
    assert result_unknown == result_default


def test_warm_conversational_is_default():
    result_implicit = build_prompt(RAW_PROMPT)
    result_explicit = build_prompt(RAW_PROMPT, style="warm-conversational")
    assert result_implicit == result_explicit


def test_formal_style_contains_professional_language():
    result = build_prompt(RAW_PROMPT, style="formal")
    assert "professional" in result.lower() or "polished" in result.lower()


def test_empathetic_style_contains_empathy_language():
    result = build_prompt(RAW_PROMPT, style="empathetic")
    assert "empathy" in result.lower() or "empathetic" in result.lower() or "feelings" in result.lower()


def test_sales_energetic_style_contains_energy_language():
    result = build_prompt(RAW_PROMPT, style="sales-energetic")
    assert "energy" in result.lower() or "enthusiasm" in result.lower() or "energetic" in result.lower()


# ---------------------------------------------------------------------------
# 3. Filler rules — AT MOST ONE filler must be enforced in every variant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ALL_STYLES)
def test_filler_rule_present_in_every_style(style: str):
    """
    The anti-stacking filler rule must appear in every style's output.
    If someone removes it from _PROSODY_HEADER, this test catches it.
    """
    result = build_prompt(RAW_PROMPT, style=style)
    assert "AT MOST ONE" in result, (
        f"Filler rule 'AT MOST ONE' missing for style '{style}'"
    )


@pytest.mark.parametrize("style", ALL_STYLES)
def test_no_chained_filler_instruction_present(style: str):
    """The explicit 'Never chain fillers' instruction must be present."""
    result = build_prompt(RAW_PROMPT, style=style)
    assert "Never chain fillers" in result or "never chain" in result.lower()


# ---------------------------------------------------------------------------
# 4. Guardrail rules — behavioral footer content
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ALL_STYLES)
def test_topic_lock_rule_in_every_style(style: str):
    result = build_prompt(RAW_PROMPT, style=style)
    assert "TOPIC LOCK" in result or "Stay strictly" in result


@pytest.mark.parametrize("style", ALL_STYLES)
def test_no_fabrication_rule_in_every_style(style: str):
    result = build_prompt(RAW_PROMPT, style=style)
    assert "NEVER fabricate" in result or "fabricate" in result.lower()


@pytest.mark.parametrize("style", ALL_STYLES)
def test_no_ai_identity_disclosure_rule(style: str):
    """Agent must never identify itself as an AI — rule must appear."""
    result = build_prompt(RAW_PROMPT, style=style)
    assert "NEVER identify yourself as an AI" in result or "language model" in result


# ---------------------------------------------------------------------------
# 5. Anti-robot phrases banned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ALL_STYLES)
def test_certainly_banned(style: str):
    result = build_prompt(RAW_PROMPT, style=style)
    assert '"Certainly!"' in result or "Certainly!" in result, (
        f"'Certainly!' ban missing for style '{style}'"
    )


@pytest.mark.parametrize("style", ALL_STYLES)
def test_great_question_banned(style: str):
    result = build_prompt(RAW_PROMPT, style=style)
    assert "Great question!" in result, (
        f"'Great question!' ban missing for style '{style}'"
    )


# ---------------------------------------------------------------------------
# 6. Empty prompt handling
# ---------------------------------------------------------------------------

def test_empty_prompt_does_not_raise():
    result = build_prompt("")
    assert isinstance(result, str)
    assert len(result) > 0


def test_empty_prompt_has_no_business_instructions_block():
    """
    When raw_prompt is empty, the '## Business Instructions' section must be
    omitted — no empty block that would confuse the LLM.
    """
    result = build_prompt("")
    assert "## Business Instructions" not in result


def test_empty_prompt_still_has_header_and_footer():
    result = build_prompt("")
    assert "Voice Delivery Instructions" in result
    assert "Strict Operating Rules" in result


def test_whitespace_only_prompt_treated_as_empty():
    result_ws = build_prompt("   \n\t  ")
    result_empty = build_prompt("")
    assert result_ws == result_empty


# ---------------------------------------------------------------------------
# 7. Idempotency — calling twice on same input returns same output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ALL_STYLES)
def test_build_prompt_is_pure(style: str):
    r1 = build_prompt(RAW_PROMPT, style=style)
    r2 = build_prompt(RAW_PROMPT, style=style)
    assert r1 == r2


# ---------------------------------------------------------------------------
# 8. Stacking guard — headers must not double-wrap
# ---------------------------------------------------------------------------

def test_header_not_duplicated_on_double_call():
    """
    If a caller accidentally passes the already-wrapped prompt back into
    build_prompt(), the prosody header must not appear twice.

    This is a regression guard: prompt_builder does not currently detect
    this — the test documents expected behaviour. If the current code
    DOES double-wrap, this test will fail and signal the need for
    idempotency protection (e.g. a sentinel comment or prefix check).
    """
    wrapped_once = build_prompt(RAW_PROMPT)
    wrapped_twice = build_prompt(wrapped_once)

    header_occurrences = wrapped_twice.count("## Voice Delivery Instructions")
    # Document current behaviour: if this is > 1, a stacking bug is present.
    assert header_occurrences == 1, (
        f"Header appeared {header_occurrences}× after double-wrapping — "
        "add idempotency guard to build_prompt()"
    )


# ---------------------------------------------------------------------------
# 9. Section separator — double newline between sections
# ---------------------------------------------------------------------------

def test_sections_joined_with_double_newline():
    result = build_prompt(RAW_PROMPT)
    # Split on double newline; every section must be non-empty
    sections = [s.strip() for s in result.split("\n\n") if s.strip()]
    assert len(sections) >= 3, (
        f"Expected ≥ 3 sections separated by double newline, got {len(sections)}"
    )


def test_no_triple_newline_between_sections():
    """No accidental triple+ blank lines — those render badly in LLM prompts."""
    result = build_prompt(RAW_PROMPT)
    assert "\n\n\n" not in result, "Triple newline found — section join is broken"
