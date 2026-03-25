# API Integration Manifest
**Voice Agent Platform — Backend v1.1.0**
**Base URL:** `https://voice-agent-platform-production-86a4.up.railway.app`
**Last updated:** 2026-03-25

---

## Authentication

All `/api/*` endpoints require a Supabase JWT in the `Authorization` header.
`/public/*` endpoints require **no auth**.
`/webhook/*` is called by Retell AI only (server-to-server).

```
Authorization: Bearer <supabase_jwt>
```

The JWT is obtained from `supabase.auth.getSession()` and injected by the Axios
request interceptor in `src/lib/api.ts` before every request leaves the browser.

---

## TypeScript Base Interfaces

```typescript
// ── Core domain types ──────────────────────────────────────────────────────

export interface AgentConfig {
  // LLM
  system_prompt: string | null;           // Raw business instructions (no prosody wrapping needed — backend handles it)
  llm_model: string;                      // Retell-native name. Default: "gpt-4o-mini"
                                          // Allowed: "gpt-4o" | "gpt-4o-mini" | "gpt-4.1" | "gpt-4.1-mini"
                                          //          | "claude-4.5-sonnet" | "claude-4.6-sonnet" | "claude-4.5-haiku"
                                          //          | "gemini-2.0-flash" | "gemini-2.5-flash"

  // Voice
  voice_id: string | null;               // Default: "11labs-Adrian"
  language: string;                       // Default: "en-US"

  // Behaviour
  business_hours: Record<string, any> | null;
  escalation_phone: string | null;
  calendar_webhook_url: string | null;
  crm_webhook_url: string | null;
  faq_knowledge_base: string | null;
  recording_enabled: boolean;             // Default: false

  // Track 1 — Tonality (prosody)
  prosody_style: "warm-conversational" | "formal" | "empathetic" | "sales-energetic";
  // Default: "warm-conversational"

  // Track 4 — Silence handling
  silence_timeout_seconds: number;        // Range: 5–60 (integer seconds). Default: 10
  max_silence_prompts: number;            // Range: 1–2 (integer). Default: 2
                                          // After this many unanswered silence prompts, agent hangs up.

  // STT — domain vocabulary boosting
  custom_vocabulary: string[];            // e.g. ["MedSync", "HbA1c", "formulary"]
                                          // Empty array = no custom terms. Default: []
}

export interface Customer {
  id: string;                             // UUID
  reseller_id: string;                    // UUID — scoped to the logged-in reseller
  name: string;
  billing_email: string;
  phone_number: string | null;            // E.164 format e.g. "+12125551234"
  plan: string;                           // "starter" | "pro" | "enterprise"
  status: string;                         // "active" | "suspended"
  retell_agent_id: string | null;         // Retell agent ID, set after creation
  created_at: string;                     // ISO 8601 UTC
}

export interface CallLog {
  id: string;                             // UUID
  customer_id: string;                    // UUID → FK to customers.id
  retell_call_id: string | null;          // Retell's own call identifier
  caller_number: string | null;           // E.164 caller ID (null for web calls)
  duration_seconds: number | null;        // Total call duration in seconds
  outcome: string | null;                 // "completed" | "transferred" | "voicemail"
                                          //  | "dropped" | "no_answer"
  transcript: string | null;             // JSON string — parse with JSON.parse()
                                          // Shape after parse: Array<{role:"agent"|"user", content:string}>
  started_at: string | null;             // ISO 8601 UTC
  ended_at: string | null;               // ISO 8601 UTC

  // ── Numeric performance metrics (raw values — UI handles styling) ──
  cost_usd: number | null;               // USD e.g. 0.0042. Multiply × 100 for cents display.
  latency_p50_ms: number | null;         // Median end-to-end response latency in milliseconds.
                                          // Good: < 800 | Acceptable: 800–1500 | Degraded: > 1500
  tokens_used: number | null;            // Total LLM tokens consumed in the call
  prosody_style_used: string | null;     // Prosody preset active at call time
}

export interface WebCallToken {
  access_token: string;                   // Passed directly to RetellWebClient.startCall()
  call_id: string;                        // Retell call ID — use for logging/tracking
}

export interface PublicAgentInfo {
  customer_id: string;
  name: string;
  has_agent: boolean;                     // false = agent not yet provisioned, disable call button
}
```

---

## Endpoint Reference

---

### 1. `POST /api/customers`

**Rationale:** Creates a new end-customer record and auto-provisions a Retell AI agent in a
single atomic flow: (1) POST to Retell `/create-retell-llm` with system prompt + model,
(2) POST to Retell `/create-agent` with voice + VAD config, (3) INSERT to `customers` table,
(4) INSERT to `agent_configs` table. Returns the `customers` row.

**Auth:** Required

**Request body:**
```json
{
  "name": "Apex Dental",
  "billing_email": "billing@apexdental.com",
  "phone_number": "+12125551234",
  "plan": "starter",
  "status": "active",
  "retell_agent_id": null,
  "agent_config": {
    "system_prompt": "You are a receptionist for Apex Dental. Help callers book appointments and answer questions about our services.",
    "llm_model": "gpt-4o-mini",
    "voice_id": "11labs-Adrian",
    "language": "en-US",
    "business_hours": {"mon-fri": "9am-6pm", "sat": "10am-2pm"},
    "escalation_phone": "+12125559999",
    "calendar_webhook_url": null,
    "crm_webhook_url": null,
    "faq_knowledge_base": null,
    "recording_enabled": false,
    "prosody_style": "warm-conversational",
    "silence_timeout_seconds": 10,
    "max_silence_prompts": 2,
    "custom_vocabulary": ["Apex Dental", "Dr. Patel", "prophylaxis", "periapical"]
  }
}
```

**Response — 201 Created:**
```json
{
  "id": "f26687e4-1ca0-4a25-a724-5d30828669d5",
  "reseller_id": "1fcd3fb0-245d-4655-a0f3-4cd90a7129af",
  "name": "Apex Dental",
  "billing_email": "billing@apexdental.com",
  "phone_number": "+12125551234",
  "plan": "starter",
  "status": "active",
  "retell_agent_id": "agent_50a01f13fedb7ece475bf475cc",
  "created_at": "2026-03-25T14:32:00.000Z"
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 400 | Validation error (e.g. `name` empty) |
| 401 | Missing or invalid JWT |
| 502 | Retell API rejected the agent creation (check `detail` field for Retell's message) |
| 500 | Supabase insert failed |

---

### 2. `GET /api/customers`

**Rationale:** Returns all customers belonging to the authenticated reseller. The `reseller_id`
is extracted from the JWT `sub` claim server-side — the frontend never needs to pass it.
Results are ordered newest-first.

**Auth:** Required

**Request:** No body. No query params.

**Response — 200 OK:**
```json
[
  {
    "id": "f26687e4-1ca0-4a25-a724-5d30828669d5",
    "reseller_id": "1fcd3fb0-245d-4655-a0f3-4cd90a7129af",
    "name": "Apex Dental",
    "billing_email": "billing@apexdental.com",
    "phone_number": "+12125551234",
    "plan": "starter",
    "status": "active",
    "retell_agent_id": "agent_50a01f13fedb7ece475bf475cc",
    "created_at": "2026-03-25T14:32:00.000Z"
  }
]
```

**Note:** `agent_configs` fields (system_prompt, prosody_style, etc.) are NOT included here.
To display or edit agent configuration, use `GET /public/agent/{id}` for metadata or
fetch from `agent_configs` directly via Supabase client if needed for the config editor.

---

### 3. `PUT /api/customers/{customer_id}/config`

**Rationale:** Updates both the Supabase `agent_configs` row AND the live Retell agent in a
single call. The backend fetches the `llm_id` from Retell dynamically, then PATCHes the
LLM's system prompt and PATCHes the agent's VAD/voice settings. `null` fields in the
request body are stripped before the Supabase update to avoid overwriting existing data.

**Auth:** Required. The `customer_id` must belong to the authenticated reseller.

**Request body:** Identical shape to `agent_config` inside `POST /api/customers`:
```json
{
  "agent_config": {
    "system_prompt": "Updated: You are a receptionist for Apex Dental...",
    "llm_model": "gpt-4o",
    "voice_id": "11labs-Adrian",
    "language": "en-US",
    "business_hours": {"mon-fri": "9am-6pm"},
    "escalation_phone": "+12125559999",
    "calendar_webhook_url": null,
    "crm_webhook_url": null,
    "faq_knowledge_base": "We accept Delta Dental and Cigna. No walk-ins.",
    "recording_enabled": false,
    "prosody_style": "empathetic",
    "silence_timeout_seconds": 15,
    "max_silence_prompts": 2,
    "custom_vocabulary": ["Apex Dental", "Dr. Patel", "prophylaxis"]
  }
}
```

**Response — 200 OK:**
Returns the updated `agent_configs` row, or `{"status": "updated"}` if Supabase returns
no row (e.g. upsert path).

**Error cases:**
| Status | Condition |
|--------|-----------|
| 400 | Customer has no retell_agent_id yet |
| 401 | Invalid JWT |
| 404 | customer_id not found or belongs to a different reseller |
| 500 | Supabase update failed (check `detail`) |
| 502 | Supabase updated OK but Retell sync failed (partial update — log and alert) |

---

### 4. `POST /api/customers/{customer_id}/web-call`

**Rationale:** Creates a short-lived Retell WebRTC access token scoped to the given customer's
agent. The frontend passes this token to `RetellWebClient.startCall()`. Auth is required
so only the owning reseller can initiate test calls from the dashboard.

**Auth:** Required. customer_id must belong to authenticated reseller.

**Request:** No body.

**Response — 200 OK:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "call_id": "call_8f3a2b1c4d5e6f7a8b9c0d1e"
}
```

**Usage in frontend:**
```typescript
import { RetellWebClient } from "retell-client-js-sdk";
const client = new RetellWebClient();
const { access_token } = await api.post(`/api/customers/${customerId}/web-call`);
await client.startCall({ accessToken: access_token });
```

---

### 5. `GET /api/calls`

**Rationale:** Returns all call logs for the authenticated reseller, joining the `calls` table
through `customers` so reseller scoping is enforced without a direct FK on `calls`.
Results are ordered most-recent-first. The backend strips the nested `customers` object
before returning so the shape is flat.

**Auth:** Required.

**Request:** No body. No query params.

**Response — 200 OK:**
```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "customer_id": "f26687e4-1ca0-4a25-a724-5d30828669d5",
    "retell_call_id": "call_8f3a2b1c4d5e6f7a8b9c0d1e",
    "caller_number": null,
    "duration_seconds": 87,
    "outcome": "completed",
    "transcript": "[{\"role\":\"agent\",\"content\":\"Hello, Apex Dental, how can I help?\"},{\"role\":\"user\",\"content\":\"I'd like to book an appointment.\"}]",
    "started_at": "2026-03-25T14:35:00.000Z",
    "ended_at": "2026-03-25T14:36:27.000Z",
    "cost_usd": 0.0038,
    "latency_p50_ms": 620,
    "tokens_used": 843,
    "prosody_style_used": "warm-conversational"
  }
]
```

**Transcript parsing:**
```typescript
const turns: Array<{role: "agent" | "user", content: string}> =
  call.transcript ? JSON.parse(call.transcript) : [];
```

**Numeric metric thresholds for UI conditional styling:**
```typescript
// latency_p50_ms
const latencyStatus = (ms: number | null) =>
  ms === null ? "unknown" :
  ms < 800    ? "good" :      // green
  ms < 1500   ? "acceptable": // yellow
                "degraded";   // red

// cost_usd — display as cents or dollars based on magnitude
const formatCost = (usd: number | null) =>
  usd === null ? "—" :
  usd < 0.01   ? `${(usd * 100).toFixed(2)}¢` :
                 `$${usd.toFixed(4)}`;

// duration_seconds — short calls may indicate silence hang-ups
const callLengthNote = (s: number | null) =>
  s !== null && s < 15 ? "possibly silence hang-up" : null;
```

---

### 6. `GET /health`

**Rationale:** Simple liveness probe used by Railway and monitoring services.

**Auth:** None.

**Response — 200 OK:**
```json
{"status": "ok"}
```

---

### 7. `GET /public/agent/{customer_id}`

**Rationale:** Returns minimal public-safe metadata for the Instant Agent Page (`/chat/:id`).
No auth required. Used to render the agent's display name and determine whether a
call button should be active (`has_agent: true`) before fetching a token.

**Auth:** None.

**Request:** No body.

**Response — 200 OK:**
```json
{
  "customer_id": "f26687e4-1ca0-4a25-a724-5d30828669d5",
  "name": "Apex Dental",
  "has_agent": true
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | customer_id does not exist |

**Frontend usage:**
```typescript
// On /chat/:customerId mount — no auth needed
const info = await axios.get(`${BASE_URL}/public/agent/${customerId}`);
if (!info.data.has_agent) {
  setError("This agent is not yet configured.");
}
```

---

### 8. `POST /public/agent/{customer_id}/token`

**Rationale:** Creates a Retell WebRTC token for the public Instant Agent Page. No auth
required — anyone with the `customer_id` UUID can initiate a call. This is intentional
for public-facing demo/deployment pages. Rate-limiting must be enforced at the
infrastructure level (Railway request limits / Vercel Edge middleware).

**Auth:** None.

**Request:** No body.

**Response — 200 OK:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "call_id": "call_8f3a2b1c4d5e6f7a8b9c0d1e"
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | customer_id not found, or agent not yet provisioned |
| 502 | Retell token creation failed |

**Complete Instant Agent Page component pattern:**
```typescript
// Route: /chat/:customerId
import { RetellWebClient } from "retell-client-js-sdk";

const client = new RetellWebClient();

export function InstantAgentPage({ customerId }: { customerId: string }) {
  const [info, setInfo] = useState<PublicAgentInfo | null>(null);
  const [status, setStatus] = useState<"idle"|"connecting"|"active"|"ended">("idle");

  useEffect(() => {
    axios.get(`${BASE_URL}/public/agent/${customerId}`)
      .then(r => setInfo(r.data));
  }, [customerId]);

  async function startCall() {
    setStatus("connecting");
    const { data } = await axios.post(`${BASE_URL}/public/agent/${customerId}/token`);
    client.on("call_started",  () => setStatus("active"));
    client.on("call_ended",    () => setStatus("ended"));
    client.on("error",         () => setStatus("idle"));
    await client.startCall({ accessToken: data.access_token });
  }

  return (
    <div>
      <h1>{info?.name ?? "Loading..."}</h1>
      <button
        disabled={!info?.has_agent || status !== "idle"}
        onClick={startCall}
      >
        {status === "idle" ? "Talk to Agent" : status}
      </button>
    </div>
  );
}
```

---

### 9. `POST /webhook/retell`  *(Retell → Backend, not browser-facing)*

**Rationale:** Receives all Retell AI lifecycle events. The backend now splits event handling
into two distinct paths based on data availability:

| Event | What happens | Data available |
|-------|-------------|----------------|
| `call_started` | Returns `{"status":"ok"}` immediately | None needed |
| `call_ended` | Clears Redis session state only | No cost/latency yet |
| `call_analyzed` | Full DB log: transcript + cost + latency + tokens | All fields populated |
| *(default)* | LLM response turn — calls OpenRouter, returns `{"response":"..."}` | Conversation transcript |

**Why split `call_ended` vs `call_analyzed`?**
Retell populates `combined_cost`, `e2e_latency`, and `call_cost.llm_tokens_used` only on
`call_analyzed` (fires ~5–10 seconds after hang-up). Logging on `call_ended` would
persist nulls for all numeric metrics. Redis cleanup still happens on `call_ended` so
session state is never orphaned.

**call_analyzed payload fields extracted:**
```typescript
// These map directly to the CallLog interface fields:
combined_cost        → cost_usd          (divided by 100 to convert cents → USD)
e2e_latency.p50      → latency_p50_ms    (integer milliseconds)
call_cost.llm_tokens_used → tokens_used  (integer)
transcript           → transcript        (serialized via JSON.stringify)
```

**Webhook LLM response shape (for reference — not called by frontend):**
```json
{"response": "Hello, Apex Dental, how can I help you today?"}
```

Or with silence hang-up:
```json
{"response": "It seems like this might not be the best time — feel free to call back!", "end_call": true}
```

---

## Complete `api.ts` Service

```typescript
import axios from "axios";
import { supabase } from "./supabase";

export const api = axios.create({
  baseURL: "https://voice-agent-platform-production-86a4.up.railway.app",
  headers: { "Content-Type": "application/json" },
});

// Inject JWT before every authenticated request
api.interceptors.request.use(async (config) => {
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    config.headers.Authorization = `Bearer ${session.access_token}`;
  }
  return config;
});

// ── Typed service functions ─────────────────────────────────────────────────

export const customerService = {
  list: ()                                    => api.get<Customer[]>("/api/customers"),
  create: (body: CreateCustomerRequest)       => api.post<Customer>("/api/customers", body),
  updateConfig: (id: string, body: UpdateAgentConfigRequest) =>
                                               api.put(`/api/customers/${id}/config`, body),
  startWebCall: (id: string)                  => api.post<WebCallToken>(`/api/customers/${id}/web-call`),
};

export const callService = {
  list: () => api.get<CallLog[]>("/api/calls"),
};

// Public — no auth interceptor needed, use plain axios
export const publicAgentService = {
  getInfo:    (customerId: string) =>
    axios.get<PublicAgentInfo>(
      `https://voice-agent-platform-production-86a4.up.railway.app/public/agent/${customerId}`
    ),
  getToken:   (customerId: string) =>
    axios.post<WebCallToken>(
      `https://voice-agent-platform-production-86a4.up.railway.app/public/agent/${customerId}/token`
    ),
};
```

---

## Field Constraints Reference

| Field | Type | Constraint | Default |
|-------|------|------------|---------|
| `silence_timeout_seconds` | `int` | 5 ≤ x ≤ 60 | 10 |
| `max_silence_prompts` | `int` | 1 ≤ x ≤ 2 | 2 |
| `prosody_style` | `string` | enum: 4 values | `"warm-conversational"` |
| `custom_vocabulary` | `string[]` | no size limit | `[]` |
| `llm_model` | `string` | Retell registry | `"gpt-4o-mini"` |
| `latency_p50_ms` | `int` | null until call_analyzed | — |
| `cost_usd` | `float` | null until call_analyzed | — |
| `tokens_used` | `int` | null until call_analyzed | — |
| `phone_number` | `string` | E.164 format | `null` |
| `plan` | `string` | `"starter"` \| `"pro"` \| `"enterprise"` | `"starter"` |
| `status` | `string` | `"active"` \| `"suspended"` | `"active"` |
