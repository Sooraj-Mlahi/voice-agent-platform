# Production Deployment Guide — Voice Agent Platform

> **Platform:** Railway (primary) / Render (alternative)
> **Last updated:** 2026-03-25
> **Stack:** FastAPI · Retell AI · OpenRouter · Supabase · Redis
> **Live URL:** `https://voice-agent-platform-production-86a4.up.railway.app`

---

## 0. Live Backend — Quick Access

The service is deployed and running. Everything below is the real URL — no placeholders.

### Interactive API docs (no auth required to browse)

| Interface | URL |
|---|---|
| Swagger UI | `https://voice-agent-platform-production-86a4.up.railway.app/docs` |
| ReDoc | `https://voice-agent-platform-production-86a4.up.railway.app/redoc` |
| Health check | `https://voice-agent-platform-production-86a4.up.railway.app/health` |

Open `/docs` in a browser to see every endpoint, try requests interactively, and inspect request/response schemas live.

### Base URL for the frontend

```
https://voice-agent-platform-production-86a4.up.railway.app
```

All protected endpoints are under `/api/*`. The public webhook endpoint is `/webhook/retell`.

### Smoke-test the live service right now

```bash
# 1. Health — no auth needed
curl https://voice-agent-platform-production-86a4.up.railway.app/health
# → {"status":"ok"}

# 2. Confirm 401 is returned without a token (auth is working)
curl https://voice-agent-platform-production-86a4.up.railway.app/api/customers
# → {"detail":"Not authenticated"}

# 3. Confirm Retell webhook endpoint accepts call_started
curl -X POST https://voice-agent-platform-production-86a4.up.railway.app/webhook/retell \
  -H "Content-Type: application/json" \
  -d '{"event":"call_started","call":{"agent_id":"test","call_id":"test"}}'
# → {"status":"ok"}
```

### Making an authenticated request from a terminal

Get your Supabase JWT first (sign in via the Supabase dashboard or your frontend), then:

```bash
TOKEN="eyJhbGc..."   # paste your JWT here

# List your customers
curl https://voice-agent-platform-production-86a4.up.railway.app/api/customers \
  -H "Authorization: Bearer $TOKEN"

# List call logs
curl https://voice-agent-platform-production-86a4.up.railway.app/api/calls \
  -H "Authorization: Bearer $TOKEN"
```

### Authenticating in Swagger UI

1. Open `https://voice-agent-platform-production-86a4.up.railway.app/docs`
2. Click **Authorize** (top right, padlock icon)
3. In the `HTTPBearer` field paste your Supabase JWT
4. Click **Authorize → Close**
5. All subsequent "Try it out" calls will include the `Authorization: Bearer` header automatically

### All live endpoints at a glance

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe |
| `GET` | `/docs` | None | Swagger UI |
| `GET` | `/redoc` | None | ReDoc |
| `GET` | `/api/customers` | JWT | List reseller's customers |
| `POST` | `/api/customers` | JWT | Create customer + Retell agent |
| `PUT` | `/api/customers/{id}/config` | JWT | Update agent config live |
| `POST` | `/api/customers/{id}/web-call` | JWT | Create WebRTC browser call token |
| `GET` | `/api/calls` | JWT | List call logs with transcripts |
| `POST` | `/webhook/retell` | Retell signature | Retell AI webhook (not for direct use) |

---

## 1. Security — Immediate Action Required

> **CRITICAL:** The previous `.env.example` contained live API keys.
> It has been replaced with placeholder values. You must:
>
> 1. **Rotate every key that was in `.env.example`** — Retell API key, OpenRouter key,
>    Supabase service key, and Supabase JWT secret — before deploying.
> 2. Verify `.env` is listed in `.gitignore` and has never been committed.
> 3. Run `git log --all --full-history -- .env` to confirm no accidental commits.

---

## 2. Required Environment Variables

Set these in Railway → Service → Variables (or Render → Environment).

### Core (required — app will not start without these)

| Variable | Example | Notes |
|---|---|---|
| `RETELL_API_KEY` | `key_xxx...` | Retell dashboard → API Keys |
| `OPENROUTER_API_KEY` | `sk-or-v1-xxx...` | OpenRouter dashboard |
| `SUPABASE_URL` | `https://<ref>.supabase.co` | Supabase → Settings → API |
| `SUPABASE_SERVICE_KEY` | `eyJhbG...` | Supabase → Settings → API → service_role key (set in Railway only — never commit) |
| `SUPABASE_JWT_SECRET` | `xxxxxxxx-...` | Supabase → Settings → API → JWT Secret |
| `REDIS_URL` | `redis://default:pwd@host:6379` | See Section 4 |
| `WEBHOOK_BASE_URL` | `https://voice-agent-platform-production-86a4.up.railway.app` | No trailing slash |

### VAD / Barge-in Tuning (override without code deploy)

| Variable | Default | Tuning guidance |
|---|---|---|
| `INTERRUPTION_SENSITIVITY` | `0.9` | `0.9` = cut agent audio quickly on user speech. Lower (e.g. `0.7`) if too hair-trigger in noisy environments. |
| `BACKCHANNEL_FREQUENCY` | `0.45` | `0.45` ≈ 45% of user turns get a "Got it." / "Sure." interject. Raise to `0.6` for warmer feel; lower to `0.3` for formal/clinical agents. |

### Silence Handling

| Variable | Default | Notes |
|---|---|---|
| `SILENCE_TIMEOUT_SECONDS` | `10` | Seconds before first "Are you still there?" fires. Min `5`, max `60`. |
| `MAX_SILENCE_PROMPTS` | `2` | Prompts before graceful hang-up. Capped at `2` by model validation. |

### Development-only (never set in production)

| Variable | Value |
|---|---|
| `DEV_MODE` | `false` — setting `true` disables JWT verification and skips Retell API calls |

---

## 3. Region Selection — Latency Critical

> **Retell AI's STT/TTS infrastructure is US-East (Virginia).
> OpenRouter's primary routing endpoint is also US-East.**

Deploy the FastAPI container to **US East** to minimise the round-trip on every webhook event.

| Platform | Where to set | Value |
|---|---|---|
| Railway | Service → Settings → Region | `us-east4` (GCP) or `us-east-1` (AWS) |
| Render | Service → Settings → Region | `Oregon (US West)` is fallback; prefer `Ohio (US East)` |

**Measured latency impact by region:**

| Region | Retell webhook RTT | Notes |
|---|---|---|
| US East | ~30–60 ms | ✅ Target |
| US West | ~80–120 ms | Acceptable |
| EU West | ~150–250 ms | Noticeable delay before TTS starts |
| AP Southeast | ~250–400 ms | Unacceptable for voice |

---

## 4. Redis — Setup and Lifecycle

### Railway setup (recommended)

1. In your Railway project, click **+ New → Database → Redis**.
2. Railway automatically injects `REDIS_URL` into services in the same project.
3. Verify in your FastAPI service's Variables tab that `REDIS_URL` is set to
   `redis://default:<password>@<internal-host>:6379`.
4. The internal Railway Redis URL (`redis://...railway.internal:...`) keeps traffic
   inside Railway's private network — no egress cost, lowest latency.

### What happens if Redis is unreachable at startup

`app/main.py` (lifespan) runs a `PING` on startup:

```
INFO  Redis connection verified at redis://...
```

If it fails:
```
WARNING  Redis ping failed: ... — ConversationState may not persist across workers.
```

The API **stays alive** — Redis failure is non-fatal by design. The consequence is that
silence prompts will still fire correctly on a single-worker deployment, but will not
share state across multiple workers. See Section 6 for worker configuration.

### Redis key schema

| Key pattern | TTL | Contents |
|---|---|---|
| `voice_agent:call:<call_id>` | 3 hours | `silence_prompt_count` (int) |

Keys are auto-expired 3 hours after last write, preventing stale accumulation.
The `clear()` method in `ConversationState` also deletes the key immediately on
`call_ended`/`call_analyzed` events, so active cleanup does not rely on TTL.

---

## 5. Dependencies

### Production (`requirements.txt`)

```
fastapi==0.110.0
uvicorn[standard]==0.29.0
httpx==0.27.0
supabase==2.4.2
python-dotenv==1.0.1
pydantic==2.6.4
pydantic-settings==2.2.1
python-jose[cryptography]==3.3.0
redis[asyncio]==5.0.4          ← async Redis client; required for ConversationState
```

`redis[asyncio]` bundles `redis-py` with the `asyncio` extra (`aioredis`-compatible
interface). The `[asyncio]` extra is required — plain `redis` will fail at runtime
when `aioredis.from_url()` is called in `main.py`.

### Test (`requirements-dev.txt`)

```
pytest==8.1.1
pytest-asyncio==0.23.6
pytest-cov==5.0.0
fakeredis[aioredis]==2.23.3    ← in-memory Redis for unit tests; no real Redis needed
respx==0.21.1                  ← httpx transport-level mock for Retell API tests
```

`fakeredis` is a **test-only** dependency. It must not be installed in production.

---

## 6. Worker Configuration

### Current `railway.json`

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level info --log-config log_config.json",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

- `--log-level info` — prevents Railway's log collector from marking startup messages as errors
- `--log-config log_config.json` — routes all Uvicorn loggers (`uvicorn`, `uvicorn.error`, `uvicorn.access`) to `stdout` so Railway sees correct severity levels

This starts a **single uvicorn process** — correct for initial deployment.

### Scaling to multiple workers

When traffic grows, switch to multiple workers. Because `ConversationState` is
Redis-backed (not in-process memory), all workers share the same silence-counter
state correctly:

```json
"startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 4 --log-level info --log-config log_config.json"
```

> **Rule of thumb:** `--workers = 2 × CPU_cores + 1`
> Railway Starter plan = 1 vCPU → `--workers 3`
> Railway Pro plan = 2 vCPU → `--workers 5`

---

## 7. Supabase — Schema Requirement

### Required tables and columns

**`customers` table**

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` (PK) | Auto-generated |
| `reseller_id` | `uuid` | FK to auth users |
| `name` | `text` | |
| `billing_email` | `text` | |
| `phone_number` | `text` | nullable |
| `plan` | `text` | default `'starter'` |
| `status` | `text` | default `'active'` |
| `retell_agent_id` | `text` | nullable — set after Retell agent creation |
| `created_at` | `timestamptz` | auto |

**`agent_configs` table**

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | `uuid` (PK) | auto | |
| `customer_id` | `uuid` (FK → customers.id) | | **The FK used by the PostgREST join** |
| `system_prompt` | `text` | null | |
| `voice_id` | `text` | `'11labs-Adrian'` | |
| `language` | `text` | `'en-US'` | |
| `llm_model` | `text` | `'openai/gpt-4o-mini'` | |
| `recording_enabled` | `bool` | `false` | |
| `business_hours` | `jsonb` | null | |
| `escalation_phone` | `text` | null | |
| `calendar_webhook_url` | `text` | null | |
| `crm_webhook_url` | `text` | null | |
| `faq_knowledge_base` | `text` | null | |
| `prosody_style` | `text` | `'warm-conversational'` | **Track 1 — add this column** |
| `silence_timeout_seconds` | `int` | `10` | **Track 4 — add this column** |
| `max_silence_prompts` | `int` | `2` | **Track 4 — add this column** |

The three columns marked **Track 1 / Track 4** were added in the latest backend release.
Run this migration in the Supabase SQL editor if the columns don't exist yet:

```sql
ALTER TABLE agent_configs
  ADD COLUMN IF NOT EXISTS prosody_style          TEXT NOT NULL DEFAULT 'warm-conversational',
  ADD COLUMN IF NOT EXISTS silence_timeout_seconds INT  NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS max_silence_prompts     INT  NOT NULL DEFAULT 2;
```

**`calls` table**

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` (PK) | |
| `customer_id` | `uuid` (FK → customers.id) | |
| `retell_call_id` | `text` | unique — used for upsert conflict target |
| `caller_number` | `text` | nullable |
| `duration_seconds` | `int` | nullable |
| `outcome` | `text` | `completed \| transferred \| dropped \| voicemail \| no_answer` |
| `transcript` | `jsonb` | array of `{role, content}` turns |
| `started_at` | `timestamptz` | |
| `ended_at` | `timestamptz` | |

### PostgREST join note

The webhook uses `customers!customer_id(id, reseller_id)` — the `!customer_id` is the explicit FK hint. If your FK column name differs, update `webhook.py:82`:

```python
.select("*, customers!<your_fk_column>(id, reseller_id)")
```

RLS is bypassed for all operations because the backend uses `SUPABASE_SERVICE_KEY` (service role). This is intentional — the backend enforces access control through JWT validation in `auth.py`.

---

## 8. Deployment Checklist

### Pre-deploy

- [ ] Rotated all credentials that were in the old `.env.example`
- [ ] `.env` is in `.gitignore` — `git status` shows no `.env` file tracked
- [ ] `WEBHOOK_BASE_URL=https://voice-agent-platform-production-86a4.up.railway.app` set in Railway Variables
- [ ] Redis service created in Railway and `REDIS_URL` auto-injected
- [ ] Railway region set to **US East**
- [ ] `DEV_MODE=false` in Railway Variables
- [ ] Supabase `agent_configs` table has `prosody_style`, `silence_timeout_seconds`, `max_silence_prompts` columns (run migration above)

### Post-deploy smoke tests

```bash
# 1. Health check
curl https://voice-agent-platform-production-86a4.up.railway.app/health
# Expected: {"status":"ok"}

# 2. Auth guard (no token → 401)
curl https://voice-agent-platform-production-86a4.up.railway.app/api/customers
# Expected: {"detail":"Not authenticated"}

# 3. Retell webhook acknowledgement
curl -X POST https://voice-agent-platform-production-86a4.up.railway.app/webhook/retell \
  -H "Content-Type: application/json" \
  -d '{"event":"call_started","call":{"agent_id":"test","call_id":"test"}}'
# Expected: {"status":"ok"}

# 4. Interactive docs reachable
open https://voice-agent-platform-production-86a4.up.railway.app/docs
```

### Verify startup logs in Railway

Railway dashboard → your service → **Deployments** → latest → **Logs**

Expected on a clean start:
```
INFO  Voice Agent Platform starting up…
INFO  HTTP client pool initialised.
INFO  Redis connection verified at redis://...
INFO  Application startup complete.
INFO  Uvicorn running on http://0.0.0.0:8080
```

All lines should show `severity: info` — not `severity: error`.

### Running the test suite before deploy

```bash
pip install -r requirements-dev.txt
pytest tests/ -v                                    # 105 tests
pytest tests/ --cov=app --cov-report=term-missing   # with coverage
```

---

## 9. Monitoring Signals

| Signal | What to watch |
|---|---|
| `WARNING Redis ping failed` in startup logs | Redis is down — silence counters won't persist across workers |
| `502 Bad Gateway` on `/webhook/retell` | OpenRouter unreachable or API key invalid |
| `404` on `/webhook/retell` with a valid agent_id | `customers!customer_id` FK join returning no rows — check schema or run the migration in Section 7 |
| `severity: error` on INFO log lines | Logs still routing to stderr — check `log_config.json` is present and `--log-config` flag is in `railway.json` |
| Flat response latency >2 s in Retell dashboard | Check Railway region (should be US East) and OpenRouter model |
| `PGRST201` error in logs | Multiple FK paths between `agent_configs` and `customers` — disambiguate FK hint in `webhook.py:82` |
| `prosody_style` / `silence_timeout_seconds` reverting to defaults | `agent_configs` table missing the three new columns — run the SQL migration in Section 7 |
