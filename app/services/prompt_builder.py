"""
PromptBuilder — wraps raw business system prompts with:
  1. A Prosody Header: TTS/voice delivery instructions for natural speech.
  2. A Behavioral Footer: strict topic-lock and anti-hallucination guardrails.

Usage:
    from app.services.prompt_builder import build_prompt
    full_prompt = build_prompt(raw_prompt, style="warm-conversational")

The `style` argument selects from pre-defined prosody tone presets.
All presets share the same sparing-filler rule — only the tonal guidance differs.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Prosody style presets
# Each value is the TONE LINE inserted in the prosody header.
# ---------------------------------------------------------------------------
_TONE_LINES: dict[str, str] = {
    "warm-conversational": (
        "Speak in a warm, conversational tone — like a knowledgeable friend, "
        "not a script reader."
    ),
    "formal": (
        "Speak in a polished, professional tone — measured and precise, "
        "like a senior customer success manager."
    ),
    "empathetic": (
        "Speak with genuine empathy — slow down slightly for emotional topics, "
        "validate the caller's feelings before moving to solutions."
    ),
    "sales-energetic": (
        "Speak with confident, positive energy — concise sentences, "
        "natural enthusiasm, outcome-focused."
    ),
}

_DEFAULT_STYLE = "warm-conversational"

# ---------------------------------------------------------------------------
# Header template
# ---------------------------------------------------------------------------
_PROSODY_HEADER = """\
## Voice Delivery Instructions (for TTS rendering)
- {tone_line}
- Fillers ("Sure thing,", "Got it,", "Of course,"): use AT MOST ONE per response \
and only at the opening turn boundary. Never chain fillers (e.g. "Sure thing, \
absolutely, got it" sounds anxious and unnatural). If the answer can start \
directly with substance, skip the filler entirely.
- Insert a brief thoughtful pause (comma or em-dash) before any answer longer \
than one sentence.
- For empathetic moments (complaints, frustration), soften your tone and slow \
down slightly even if your base style is energetic.
- Vary sentence length: mix short punchy answers with flowing explanations.
- Never say "Certainly!" or "Great question!" — they sound robotic every time.
- End confirmations with a natural check-in: "...does that work for you?" or \
"...shall I go ahead?"
- Speak numbers and dates naturally: "March twenty-fourth" not "03/24"; \
"fifteen dollars" not "$15".\
"""

# ---------------------------------------------------------------------------
# Behavioral footer (guardrails — constant across all styles)
# ---------------------------------------------------------------------------
_BEHAVIORAL_FOOTER = """\
## Strict Operating Rules
- TOPIC LOCK: Stay strictly within the scope of your role. If the user asks \
something outside your knowledge or role, redirect politely: "I'm set up \
specifically to help with [business purpose] — let me focus on that for you."
- NEVER fabricate facts, prices, names, dates, or policies not explicitly \
provided in your knowledge base or earlier in this conversation.
- If you don't know something: "I want to make sure you get accurate \
information — let me have the right person follow up with you on that."
- NEVER identify yourself as an AI, a language model, or mention your training. \
If asked, say: "I'm the virtual assistant for [business name] — how can I help?"\
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_prompt(raw_prompt: str, style: str = _DEFAULT_STYLE) -> str:
    """
    Wrap `raw_prompt` with the prosody header and behavioral footer.

    Idempotent: if the prompt already starts with the prosody header sentinel
    (i.e. it was already wrapped), it is returned unchanged. This prevents
    double-wrapping when a caller passes an already-assembled prompt back in.

    Args:
        raw_prompt: The plain business system prompt stored in agent_configs.
        style:      Prosody tone preset key. Defaults to "warm-conversational".
                    Unknown styles silently fall back to the default.

    Returns:
        A fully assembled system prompt string ready to send to the LLM.
    """
    # Idempotency guard — sentinel is the fixed heading of _PROSODY_HEADER
    if raw_prompt.strip().startswith("## Voice Delivery Instructions"):
        return raw_prompt

    tone_line = _TONE_LINES.get(style, _TONE_LINES[_DEFAULT_STYLE])
    header = _PROSODY_HEADER.format(tone_line=tone_line)

    sections = [header]
    if raw_prompt.strip():
        sections.append(f"## Business Instructions\n{raw_prompt.strip()}")
    sections.append(_BEHAVIORAL_FOOTER)

    return "\n\n".join(sections)
