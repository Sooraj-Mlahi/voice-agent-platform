# Production Deployment Guide — Voice Agent Platform

> **Platform:** Railway (primary) / Render (alternative)
> **Last updated:** 2026-03-24
> **Stack:** FastAPI · Retell AI · OpenRouter · Supabase · Redis

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
| `SUPABASE_SERVICE_KEY` | `eyJhbG...` | Supabase → Settings → API → service_role key |
| `SUPABASE_JWT_SECRET` | `xxxxxxxx-...` | Supabase → Settings → API → JWT Secret |
| `REDIS_URL` | `redis://default:pwd@host:6379` | See Section 4 |
| `WEBHOOK_BASE_URL` | `https://<service>.up.railway.app` | No trailing slash |

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

`app/main.py` (`lifespan`, line 67–73) runs a `PING` on startup:

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

### Railway `railway.json` (current)

```json
{
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

This starts a **single uvicorn process** — safe for initial deployment.

### Scaling to multiple workers

When traffic grows, switch to multiple workers. Because `ConversationState` is
Redis-backed (not in-process memory), all workers share the same silence-counter
state correctly:

```json
{
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 4",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

> **Rule of thumb:** set `--workers` to `2 × CPU_cores + 1`.
> Railway Starter plan = 1 vCPU → `--workers 3`.
> Railway Pro plan = 2 vCPU → `--workers 5`.

Alternatively, deploy multiple Railway replicas with `--workers 1` per replica —
Railway load-balances across them and Redis keeps state consistent.

---

## 7. Supabase — Schema Requirement

The fused join in `webhook.py` (`_fetch_agent_config`) requires:

1. A table `agent_configs` with a `customer_id` column that is a **foreign key**
   to `customers.id`.
2. A column `retell_agent_id` on the `customers` table.

The join selector `customers!customer_id(id, reseller_id)` explicitly names the FK
column. If your FK column is named differently, update line 77 of `webhook.py`:

```python
# Change `customer_id` to your actual FK column name
.select("*, customers!<your_fk_column>(id, reseller_id)")
```

PostgREST requires Row Level Security (RLS) to be compatible with the service role
key. Since this platform uses `SUPABASE_SERVICE_KEY` (service role), RLS policies
are bypassed — this is intentional for a backend-only service.

---

## 8. Deployment Checklist

### Pre-deploy

- [ ] Rotated all credentials that were in the old `.env.example`
- [ ] `.env` is in `.gitignore`; `git status` shows no `.env` file tracked
- [ ] `WEBHOOK_BASE_URL` is set to the deployed service's public URL
- [ ] Redis service created in Railway (or external Redis URL configured)
- [ ] Railway region set to **US East**
- [ ] `DEV_MODE=false` in production variables

### Post-deploy smoke tests

```bash
# 1. Health check
curl https://<your-service>.up.railway.app/health
# Expected: {"status":"ok"}

# 2. Check startup logs for Redis confirmation
# Railway → Service → Deployments → latest → Logs
# Expected: "INFO Redis connection verified at redis://..."

# 3. Verify webhook URL is reachable (Retell needs to POST to it)
curl -X POST https://<your-service>.up.railway.app/webhook/retell \
  -H "Content-Type: application/json" \
  -d '{"event":"call_started","call":{"agent_id":"test","call_id":"test"}}'
# Expected: {"status":"ok"}
```

### Running the test suite before deploy

```bash
# Install test deps (never in production image)
pip install -r requirements-dev.txt

# Run all 105 tests
pytest tests/ -v

# With coverage report
pytest tests/ --cov=app --cov-report=term-missing
```

---

## 9. Monitoring Signals

| Signal | What to watch |
|---|---|
| `WARNING Redis ping failed` in startup logs | Redis is down — silence counters won't persist across workers |
| `502 Bad Gateway` on `/webhook/retell` | OpenRouter unreachable or API key invalid |
| `404` on `/webhook/retell` with a valid agent_id | `customers!customer_id` FK join returning no rows — check schema |
| Flat response latency >2 s in Retell dashboard | Check Railway region (should be US East) and OpenRouter model |
| `PGRST201` error in logs | Multiple FK paths between `agent_configs` and `customers` — disambiguate FK hint in `webhook.py:77` |
