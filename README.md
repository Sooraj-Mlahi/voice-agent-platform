# Voice Agent Platform

A production-ready Python FastAPI backend for a multi-tenant voice agent platform powered by Retell AI, OpenRouter LLMs, and Supabase.

## Architecture

```
voice-agent-platform/
├── app/
│   ├── main.py            # FastAPI app, middleware, routers
│   ├── config.py          # Settings (env vars via pydantic-settings)
│   ├── database.py        # Supabase singleton client
│   ├── auth.py            # JWT Bearer auth dependency
│   ├── models.py          # Pydantic request/response models
│   ├── services/
│   │   ├── retell.py      # Retell AI API client
│   │   └── openrouter.py  # OpenRouter LLM client
│   └── routers/
│       ├── webhook.py     # POST /webhook/retell
│       ├── customers.py   # POST/GET /api/customers, PUT /api/customers/:id/config
│       └── calls.py       # GET /api/calls
├── requirements.txt
├── render.yaml
└── .env.example
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/webhook/retell` | None | Retell AI webhook (call events + LLM) |
| `POST` | `/api/customers` | Bearer JWT | Create customer & Retell agent |
| `GET` | `/api/customers` | Bearer JWT | List reseller's customers |
| `PUT` | `/api/customers/{id}/config` | Bearer JWT | Update agent config |
| `GET` | `/api/calls` | Bearer JWT | List reseller's call logs |
| `GET` | `/health` | None | Health check |

## Supabase Schema

Run these SQL statements in your Supabase SQL editor:

```sql
-- Customers table
create table customers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  phone_number text not null,
  business_name text default '',
  reseller_id uuid not null,
  retell_agent_id text,
  agent_config jsonb not null default '{}'::jsonb,
  created_at timestamptz default now()
);

-- Call logs table
create table call_logs (
  id uuid primary key default gen_random_uuid(),
  call_id text unique not null,
  agent_id text not null,
  customer_id uuid references customers(id),
  reseller_id uuid not null,
  transcript jsonb not null default '[]'::jsonb,
  duration_seconds int,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz default now()
);

-- Row Level Security (optional — backend uses service key)
alter table customers enable row level security;
alter table call_logs enable row level security;
```

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `RETELL_API_KEY` | Retell AI API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `SUPABASE_JWT_SECRET` | Supabase JWT secret (for token verification) |

## Local Development

```bash
# Create and activate virtual environment
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and fill in env vars
cp .env.example .env

# Run the server
uvicorn app.main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

## Deploying to Render.com

1. Push this repo to GitHub.
2. In Render.com dashboard → **New → Blueprint** → connect your repo.
3. Render will detect `render.yaml` and configure the service automatically.
4. Add environment variable values in the Render dashboard under the service's **Environment** tab.
5. Retell webhook URL to configure: `https://your-render-url.onrender.com/webhook/retell`
