# Voice Agent Platform — Critical Upgrades: UPDATE_PLAN

> **Status:** Awaiting Approval  
> **Last Updated:** 2026-03-24  
> **Scope:** Four upgrade tracks — Human Tonality, Barge-in Logic, Latency Reduction, State-bound Conversational Flow

---

## Executive Summary

After a full audit of the codebase (`app/services/openrouter.py`, `app/services/retell.py`, `app/routers/webhook.py`, `app/models.py`, `app/config.py`), the following architectural gaps and bottlenecks were identified:

| # | Gap | Severity |
|---|-----|----------|
| 1 | `system_prompt` is a plain, bare text string — no prosody, fillers, or emotional cues | 🔴 High |
| 2 | No VAD / barge-in handling — agent plays audio to completion regardless of user speech | 🔴 High |
| 3 | `openrouter.py` uses full buffer-and-return (waits for entire LLM completion before responding) | 🔴 High |
| 4 | `httpx` clients are created per-request (no connection reuse) | 🟡 Medium |
| 5 | No silence detection, fallback prompts, or graceful hang-up logic | 🔴 High |
| 6 | No topic-lock or guardrail mechanism in the system prompt or webhook handler | 🟡 Medium |
| 7 | `timeout=60.0` on OpenRouter calls — worst-case full minute before failure | 🟡 Medium |
| 8 | Retell LLM creation uses `gpt-4o-mini` by default but no fast "filler" pre-computation exists | 🟡 Medium |

---

## Upgrade Track 1 — Human-like Tonality

### Problem
The `system_prompt` stored in `agent_configs` is passed verbatim to OpenRouter as a raw `system` message. There are zero prosody instructions, no emotional cues, and no SSML guidance for the downstream TTS engine (ElevenLabs via Retell). This results in flat, robotic delivery.

### Changes

#### `app/services/prompt_builder.py` — **[NEW FILE]**
Create a `PromptBuilder` class that wraps any raw business system prompt with a **Prosody Header** and a **Behavioral Footer**.

**Prosody Header to prepend:**
```
## Voice Delivery Instructions (for TTS rendering)
- Speak in a warm, conversational tone — like a knowledgeable friend, not a script reader.
- Fillers ("Sure thing,", "Got it,", "Of course,"): use AT MOST ONE per response and only at the
  opening turn boundary. Never chain fillers (e.g., "Sure thing, absolutely, got it" sounds anxious
  and unnatural). If the answer can start directly with substance, skip the filler entirely.
- Insert a brief thoughtful pause (comma or em-dash) before any answer longer than one sentence.
- For empathetic moments (complaints, frustration), soften your tone and slow down slightly.
- Vary sentence length: mix short punchy answers with flowing explanations.
- Never say "Certainly!" or "Great question!" — they sound robotic.
- End confirmations with rising intonation cues: "...does that work for you?"
- Numbers and dates: say them naturally ("March twenty-fourth" not "03/24").
```

**Behavioral Footer to append:**
```
## Strict Operating Rules
- Stay strictly on topic. deflect any off-topic question with: "I'm here specifically for [business purpose], let me help with that."
- NEVER fabricate information not in your knowledge base.
- If you don't know something, say: "Let me make sure I get you the right info — can I have someone follow up?"
```

**`build_prompt(raw_prompt: str) -> str`** — concatenates header + raw_prompt + footer.

#### `app/routers/webhook.py` — **[MODIFY]**
- Line 178: Replace `system_prompt = config.get("system_prompt", "You are a helpful assistant.")` with `system_prompt = prompt_builder.build_prompt(config.get("system_prompt", ""))`.

#### `app/models.py` — **[MODIFY]**
- Add `prosody_style: str = Field(default="warm-conversational")` to `AgentConfig`. This field will allow per-agent prosody override (e.g., `"formal"`, `"empathetic"`, `"sales-energetic"`) to be passed to `build_prompt()`.

#### `app/services/retell.py` — **[MODIFY]**
- When calling `_create_retell_llm`, wrap the system prompt through `prompt_builder.build_prompt()` so even agents configured via direct Retell creation benefit.

---

## Upgrade Track 2 — Barge-in / VAD Management

### Problem
The current architecture has **no barge-in mechanism**. Retell AI natively supports it, but the platform must configure it correctly on the agent — and the webhook handler must handle the `utterance_end_method` and turncutting parameters.

### Changes

#### `app/services/retell.py` — **[MODIFY]**

On both `_create_retell_llm` and `create_retell_agent`, add the following fields to the agent creation payload:

```python
# Barge-in & VAD settings
"enable_backchannel": True,            # Agent says "uh-huh", "got it" while user speaks
"backchannel_frequency": 0.45,         # ~45% chance — natural without sounding anxious
"backchannel_words": ["Got it.", "Sure.", "One moment."],  # "Uh-huh" removed (sounds nervous at scale)
"end_call_after_silence_ms": 600000,   # Let silence handler (Track 4) manage hang-up, not Retell
"ambient_sound": None,
"interruption_sensitivity": 0.9,       # High: triggers barge-in quickly on user speech
"reminder_trigger_ms": 10000,          # 10 seconds before reminder fires
"reminder_max_count": 2,              # Exactly 2 "Are you still there?" prompts
```

**Explanation:**
- `interruption_sensitivity` at `0.9` means Retell's built-in VAD will cut the agent's audio playback immediately when the user voice energy exceeds the threshold.
- `enable_backchannel` makes the agent feel alive — it interjects natural affirmations while the user is still speaking.
- `reminder_trigger_ms` + `reminder_max_count` maps directly to the silence handling requirement (Track 4).

#### `app/services/retell.py` — `update_retell_agent()` — **[MODIFY]**
- Also patch these barge-in/VAD parameters when updating an existing agent so legacy agents get upgraded.

#### `app/config.py` — **[MODIFY]**
- Add `interruption_sensitivity: float = 0.9` and `backchannel_frequency: float = 0.8` as configurable env vars so they can be tuned without code deploys.

---

## Upgrade Track 3 — Latency Reduction

### Identified Bottlenecks

| Bottleneck | Location | Latency Impact |
|---|---|---|
| Full LLM response buffered before return | `openrouter.py:56` — `timeout=60.0`, no `stream=True` | **+1,500–4,000ms** on complex queries |
| New `httpx.AsyncClient` per request | `retell.py` & `openrouter.py` — `async with httpx.AsyncClient(...)` inside every function | **+50–200ms** per call (TLS handshake overhead) |
| Two sequential Supabase queries in `_fetch_agent_config()` | `webhook.py:40-75` — first fetch customer, then fetch config | **+100–300ms** |
| Two sequential Retell API calls to create agent | `retell.py:67-93` — `_create_retell_llm` then `create_agent` | **+400–800ms** (only at creation, not runtime) |
| Model: `gpt-4o` default in `AgentConfig` | `models.py:40` — `llm_model: str = Field(default="gpt-4o")` | **+500–1,000ms** vs. faster models |

### Changes

#### `app/services/openrouter.py` — **[MODIFY]** — Streaming

Add a new `chat_completion_stream()` function that sets `stream=True` and returns an async generator of string tokens via SSE parsing:

```python
# NEW: streaming variant
async def chat_completion_stream(model, system_prompt, messages, temperature) -> AsyncGenerator[str, None]:
    payload["stream"] = True
    async with http_client.stream("POST", f"{OPENROUTER_BASE_URL}/chat/completions", ...) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                delta = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta
```

The existing `chat_completion()` (blocking) is **kept** for non-realtime paths (e.g., dev-mode testing). Only the webhook LLM event path switches to the stream variant.

#### `app/routers/webhook.py` — **[MODIFY]** — Sentence Detection & First-Sentence Flush

This is a dedicated step within the LLM response handler. After calling `chat_completion_stream()`, the webhook accumulates tokens until it detects the **first sentence boundary** (`.`, `?`, `!` followed by a space or end-of-stream), then immediately returns that sentence to Retell so TTS synthesis can begin. The remainder of the response is collected and sent as a follow-up turn.

```python
SENTENCE_END = re.compile(r'[.?!](?:\s|$)')

buffer = ""
first_sentence = None
remainder = ""

async for token in openrouter.chat_completion_stream(...):
    buffer += token
    if first_sentence is None:
        match = SENTENCE_END.search(buffer)
        if match:
            # Split at the first sentence boundary
            first_sentence = buffer[:match.end()].strip()
            remainder = buffer[match.end():]
    else:
        remainder += token

# Flush first sentence to Retell immediately (lowest latency)
# Retell begins TTS synthesis on this while the rest continues
response_text = first_sentence or buffer   # fallback: short answers with no punctuation
# remainder is appended and sent in the same response payload
if remainder.strip():
    response_text = f"{first_sentence} {remainder.strip()}"
```

**Why this matters for the current codebase:** `openrouter.py` currently calls `await client.post()` with `timeout=60.0` and waits for the full response (line 55–62). There is zero streaming infrastructure today. This change directly targets that bottleneck — the first sentence typically arrives in **300–600ms** instead of **1,500–4,000ms** for a full buffered response.

#### `app/main.py` — **[MODIFY]** — Persistent HTTP Client (Connection Pooling)

Create a single `httpx.AsyncClient` at app startup using the `lifespan` context manager and store it in `app.state`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    yield
    await app.state.http_client.aclose()
```

Inject this client into `openrouter.py` and `retell.py` via a dependency instead of creating a new one per call.

#### `app/routers/webhook.py` — **[MODIFY]** — Fused Supabase Query

Replace the two sequential DB calls in `_fetch_agent_config()` with a single joined query:

```python
result = (
    db.table("agent_configs")
    .select("*, customers!inner(id, reseller_id)")
    .eq("customers.retell_agent_id", agent_id)
    .single()
    .execute()
)
```

This eliminates one full round-trip to Supabase.

#### `app/models.py` / `app/services/retell.py` — **[MODIFY]** — Model Default

Change default `llm_model` to `"openai/gpt-4o-mini"`. This alone can shave **500–800ms** off median response latency for short conversational turns:

```python
llm_model: str = Field(default="openai/gpt-4o-mini")
```

> **Note on Infrastructure Proximity:** Retell AI's STT/TTS is US-based. If deploying to Railway/Render, choose the **US East** region for the FastAPI container. This reduces webhook round-trip latency by **50–150ms** compared to EU or AP regions.

---

## Upgrade Track 4 — State-bound Conversational Flow

### Problem
There is zero state management in the webhook handler. The agent doesn't know:
- Whether it's waiting for a user response
- How long the silence has lasted
- Whether it has already sent a "Are you still there?" prompt
- Whether to hang up

### Changes

#### `app/services/conversation_state.py` — **[NEW FILE]**

An in-memory (or Redis-backed for multi-instance deployments) state store keyed by `call_id`:

```python
class ConversationState:
    call_id: str
    silence_prompt_count: int = 0   # 0 → 1 → 2 → hang up
    last_user_turn_at: datetime
    topic_locked: bool = True
    is_active: bool = True
```

- `get_state(call_id)` — create if not exists
- `record_user_turn(call_id)` — reset silence counter, update timestamp
- `increment_silence(call_id) -> int` — returns new count (1 or 2 → prompt, 3 → hang up signal)
- `clear_state(call_id)` — called on `call_ended` event

#### `app/routers/webhook.py` — **[MODIFY]** — Silence & Fallback Logic

On the LLM response event, check for silence signals from Retell (they appear as `utterance_end_method: "silence"` or via the `reminder_trigger_ms` callback):

```python
# Silence handling within the LLM event handler
state = conversation_state.get_state(call_id)
silence_count = conversation_state.increment_silence(call_id)

if silence_count == 1:
    return {"response": "Are you still there? Take your time — I'm right here whenever you're ready."}
elif silence_count == 2:
    return {"response": "Just checking in one more time — are you still with me?"}
elif silence_count >= 3:
    return {
        "response": "It seems like this might not be the best time. Feel free to call back whenever you're ready — have a great day!",
        "end_call": True   # Retell honors this to gracefully hang up
    }
```

#### `app/routers/webhook.py` — **[MODIFY]** — Topic-Lock Guard

After extracting `response_text` from OpenRouter, add a post-generation content check:

```python
FORBIDDEN_DRIFT_PHRASES = [
    "as an AI language model",
    "I was trained by",
    "my knowledge cutoff",
    "as ChatGPT",
]

def _is_on_topic(text: str) -> bool:
    lower = text.lower()
    return not any(phrase in lower for phrase in FORBIDDEN_DRIFT_PHRASES)

if not _is_on_topic(response_text):
    response_text = "I'm here to help with questions about [business]. Is there something specific I can assist you with?"
```

#### `app/models.py` — **[MODIFY]**
- Add `silence_timeout_seconds: int = Field(default=10)` to `AgentConfig` — controls `reminder_trigger_ms` sent to Retell.
- Add `max_silence_prompts: int = Field(default=2)` — configures the two-prompt limit before hang-up.

#### `app/services/retell.py` — **[MODIFY]**
- Map `silence_timeout_seconds` → `reminder_trigger_ms = silence_timeout_seconds * 1000` in the agent payload dynamically, using the per-customer config value instead of a hardcoded constant.

---

## New Files Summary

| File | Type | Purpose |
|---|---|---|
| `app/services/prompt_builder.py` | NEW | Wraps system prompts with prosody header + guardrail footer |
| `app/services/conversation_state.py` | NEW | In-memory call state tracker (silence count, topic lock) |

## Modified Files Summary

| File | Changes |
|---|---|
| `app/services/openrouter.py` | Add `chat_completion_stream()` (SSE), keep blocking `chat_completion()` for non-realtime paths |
| `app/services/retell.py` | Add VAD/barge-in params (backchannel_frequency → 0.45), `prompt_builder` wrapping, `silence_timeout_seconds` mapping |
| `app/routers/webhook.py` | Fused DB query, **sentence detection + first-sentence flush**, silence state handling, topic-lock guard |
| `app/models.py` | Add `prosody_style`, `silence_timeout_seconds`, `max_silence_prompts` fields |
| `app/config.py` | Add `interruption_sensitivity`, `backchannel_frequency` env vars |
| `app/main.py` | Persistent `httpx.AsyncClient` via lifespan, injected via `app.state` |

---

## Verification Plan

### Automated (Pytest)
1. **`tests/test_prompt_builder.py`** — Assert prosody header is prepended and guardrail footer is appended. Assert `build_prompt("")` does not crash.
2. **`tests/test_conversation_state.py`** — Assert silence counter increments correctly, resets on user turn, returns correct responses at counts 1, 2, 3+.
3. **`tests/test_topic_lock.py`** — Assert `_is_on_topic()` returns `False` for known drift phrases.
4. **`tests/test_openrouter_stream.py`** — Mock `httpx` stream and assert the generator yields token chunks.

### Manual / Integration
1. Set `DEV_MODE=true` in `.env` and call `POST /api/customers` — verify no Retell API credit consumed.
2. Use the existing `POST /webhook/retell` endpoint with a mock payload. Confirm the response JSON includes the prosody-enhanced system prompt flowing through `prompt_builder`.
3. Create a live test call via `POST /api/customers/{id}/web-call` in the admin panel. Speak, then go silent for 10s. Confirm the agent says "Are you still there?" Remain silent again — confirm second prompt. Remain silent — confirm graceful hang-up.
4. During the same test call, interrupt the agent mid-sentence and confirm the agent immediately acknowledges the interruption (barge-in working).

---

## Open Questions for Approval

1. **Silence Timeout:** Should the default silence timeout before triggering the first "Are you still there?" be `10` seconds or a different value?
2. **Streaming:** Retell's `retell-llm` response engine (used here) may not natively support chunked streaming over its webhook protocol. If not, the latency gain comes from the **first-sentence boundary** optimization only. Should we fall back to that, or confirm Retell supports full SSE?
3. **State Persistence:** The `ConversationState` service uses in-memory storage. For multi-worker deployments (e.g., Railway with 2+ workers), this will not work correctly. Should we add a `Redis` dependency now, or accept in-memory for V1?
4. **Topic Lock vocabulary:** The business topic context (e.g., "I'm here to help with your dental appointment") is currently generic. Should there be a `business_context` field in `AgentConfig` to make the fallback message specific?

---

*Awaiting your approval before implementation begins.*
