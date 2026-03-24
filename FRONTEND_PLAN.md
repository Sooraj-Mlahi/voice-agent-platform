# Goal Description
The objective is to build an independent Single Page Application (SPA) frontend that acts as the Admin Dashboard for "Resellers." This frontend will securely consume the newly built FastAPI backend to create and manage Retell AI agents, configurations, and view call transcripts.

## Rationale for SPA (Vite + React)
Since the backend is a standalone FastAPI REST API, a pure SPA is the fastest, cleanest, and cheapest way to deploy the frontend. It can be hosted on free CDNs (Vercel, GitHub Pages) and will rely purely on client-side fetching `Axios/Fetch` using Supabase JWT tokens for authorization.

---

## Proposed Changes

### Phase 1: Foundation & Tech Setup
1. **Initialize Project:** Run `npm create vite@latest voice-agent-platform-front-end --template react-ts`.
2. **Move to Directory:** `cd voice-agent-platform-front-end`
3. **Styling System:** Install and configure **Tailwind CSS** alongside **shadcn/ui** (or standard MUI) to instantly get beautiful, accessible dashboard components (Tables, Modals, Inputs).
4. **Routing:** Install `react-router-dom` to handle SPA navigation (`/login`, `/dashboard`, `/customers/:id`).
5. **State Management:** Use standard React Context for Auth, and `TanStack React Query` for fetching/caching data from the FastAPI backend.

### Phase 2: Authentication Layer (Supabase Auth + Google Sign In)
1. **Supabase Client:** Install `@supabase/supabase-js`.
2. **Auth Context:** Create an `AuthProvider.tsx` that manages the user session. 
3. **Login View (Google OAuth):** Create `Login.tsx` with a single "Sign in with Google" button. The frontend will call `supabase.auth.signInWithOAuth({ provider: 'google' })`.
   * *Note on Backend limits: Because our FastAPI backend relies exclusively on the generated JWT rather than tracking the sign-in intent, Google Sign In requires ZERO changes to the Python code! The backend simply decodes the Google-issued Supabase token automatically.*
4. **JWT Extraction:** When a Reseller logs in successfully, extract their `session.access_token` (JWT).

### Phase 3: API Integration Service (The Bridge)
Create an `api.ts` file using `Axios`.
1. **API Base URL:** Permanently configure Axios to target the live API: `https://voice-agent-platform-production-86a4.up.railway.app/api`.
2. **Axios Interceptor:** Write an interceptor that automatically attaches the Supabase JWT (`Authorization: Bearer <token>`) to every outgoing request heading to the FastAPI backend.
3. This ensures the backend `get_current_reseller` dependency always passes.

### Phase 4: Core Layout & Navigation
1. **Protected Routes:** Ensure a user must be authenticated before viewing `/dashboard`.
2. **Sidebar:** A persistent layout with links to:
   * **Dashboard** (Overview stats)
   * **Customers / Agents** (Manage agents)
   * **Call Logs** (Transcripts)

### Phase 5: Building the Views

#### 5.1 Customers List (`/customers`)
- Calls `GET /api/customers`.
- Displays a Tailwind table: `name`, `billing_email`, `plan`, `status`, `retell_agent_id`.
- **"Create New Customer"** button opens the Creation Modal (see 5.2).
- Each row has an **"Edit Config"** link → `/customers/:id/config`.

#### 5.2 Customer Creation Modal
- Form fields: `name`, `billing_email`, `phone_number` (E.164), `plan`, `status`.
- Nested **Agent Config section** — same fields as the Config Editor below (collapsed by default, expandable).
- Submits `POST /api/customers` with the full `AgentConfig` payload.
- On success: close modal, refetch customers list via React Query invalidation.

#### 5.3 Enhanced Agent Config Editor (`/customers/:id/config`)

Fetches the current config via `GET /api/customers` (filter by id client-side) and submits changes via `PUT /api/customers/{id}/config`.

The editor is split into four labelled sections:

---

**Section A — Agent Identity**

| Field | UI Control | Values |
|---|---|---|
| `system_prompt` | `<textarea>` (min-h-40) | Free text |
| `voice_id` | `<Select>` dropdown | e.g. `11labs-Adrian`, `11labs-Rachel` |
| `llm_model` | `<Select>` dropdown | `openai/gpt-4o-mini` (default), `openai/gpt-4o`, `anthropic/claude-3-haiku` |
| `language` | `<Select>` dropdown | `en-US`, `en-GB`, `es-ES`, etc. |

---

**Section B — Voice Personality (Track 1: Tonality)**

| Field | UI Control | Type | Default | Notes |
|---|---|---|---|---|
| `prosody_style` | `<Select>` with descriptive labels | `string` | `warm-conversational` | See options below |

`prosody_style` dropdown options:

```
warm-conversational  → "Warm & Conversational (Recommended)"
formal               → "Formal & Professional"
empathetic           → "Empathetic & Supportive"
sales-energetic      → "Sales-Energetic"
```

Display a helper callout beneath the dropdown:
> *"This controls how the agent phrases responses and varies sentence rhythm. It does not change the voice itself — use Voice ID for that."*

---

**Section C — Barge-in & Interruption (Track 2: VAD)**

> **Note:** `interruption_sensitivity` and `backchannel_frequency` are currently **deployment-level** settings (Railway env vars), not per-customer fields. They are NOT in `AgentConfig` and cannot be changed per-agent from the UI without a backend schema extension. Render them as **read-only display values** sourced from a `/api/config` endpoint (to be added), or omit until that endpoint exists.

| Field | UI Control | Type | Range | Default |
|---|---|---|---|---|
| `interruption_sensitivity` *(read-only)* | `<Slider>` (disabled) | `float` | 0.0 – 1.0 | `0.9` |
| `backchannel_frequency` *(read-only)* | `<Slider>` (disabled) | `float` | 0.0 – 1.0 | `0.45` |

Add a tooltip: *"These values are set globally in your deployment. Contact your admin to change them."*

---

**Section D — Silence & Hang-up Behaviour (Track 4: State Flow)**

| Field | UI Control | Type | Range | Default | Description |
|---|---|---|---|---|---|
| `silence_timeout_seconds` | `<Slider>` with tick marks | `integer` | 5 – 60 s | `10` | How long the agent waits before saying "Are you still there?" |
| `max_silence_prompts` | `<RadioGroup>` (1 or 2) | `integer` | 1 – 2 | `2` | How many reminders before graceful hang-up |

Display the silence flow as a visual diagram beneath the controls:

```
Silence detected
    ↓ (after silence_timeout_seconds)
"Are you still there?" [prompt 1]
    ↓ (silence_timeout_seconds again)
"Just checking in..." [prompt 2 — only if max_silence_prompts = 2]
    ↓
Graceful hang-up ("Have a great day!")
```

Update the diagram live as the slider moves: if `max_silence_prompts = 1`, grey out the second prompt node.

---

**Submit behaviour:** Show a loading spinner on `PUT /api/customers/{id}/config`. On success, show a toast: *"Agent updated — changes are live immediately."* On 502, show: *"Config saved locally but Retell sync failed — changes will apply on next call."*

---

#### 5.4 Call Logs (`/calls`)

Calls `GET /api/calls`. Displays a sortable, filterable table.

**Table columns:**

| Column | Source field | Notes |
|---|---|---|
| Date/Time | `started_at` | Format: `MMM D, YYYY h:mm A` |
| Caller | `caller_number` | Show "Unknown" if null |
| Duration | `duration_seconds` | Format as `m:ss` |
| Outcome | `outcome` | Coloured badge (see below) |
| Latency | `duration_seconds` / `transcript` | See Latency Indicator below |
| Actions | — | Expand transcript, copy call ID |

**Outcome badge colours:**

| `outcome` value | Badge colour | Label |
|---|---|---|
| `completed` | Green | Completed |
| `transferred` | Blue | Transferred |
| `no_answer` | Yellow | No Answer |
| `dropped` | Red | Dropped |
| `voicemail` | Grey | Voicemail |
| *(any other)* | Grey | Unknown |

> **Silence hang-up detection:** When the backend's Redis state machine triggers `end_call: True`, Retell disconnects and the resulting call record lands in `calls` with `outcome: "completed"` (current mapping). To surface these distinctly, look for calls where `duration_seconds` is short (< 60 s) combined with a transcript ending in the hang-up phrase. A future backend change should add `outcome: "silence_hangup"` — leave a `// TODO` comment in the component for this.

---

**Latency Indicator (Track 3: First-Sentence Flush)**

Add a **"Response Latency"** column to the Call Logs table. This is a derived, estimated metric — not a raw backend field. Calculate it client-side:

```ts
// Proxy metric: words-per-second of the first agent turn in the transcript
// Higher = faster perceived response; lower = agent was slow to start
const firstAgentTurn = transcript.find(t => t.role === "agent");
const wordCount = firstAgentTurn?.content.split(" ").length ?? 0;
const estimatedLatencyMs = duration_seconds > 0
  ? Math.round((duration_seconds * 1000) / wordCount)
  : null;
```

Display as a coloured pill:

| Estimated latency | Colour | Label |
|---|---|---|
| < 500 ms | Green | Fast |
| 500 – 1200 ms | Yellow | Normal |
| > 1200 ms | Red | Slow |
| null / no transcript | Grey | — |

Add a tooltip on hover: *"Estimated time-to-first-word. Lower is better. Powered by streaming LLM response."*

---

**Transcript Expander**

Clicking a row expands an inline transcript viewer:
- Render each turn as a chat bubble (`role: "agent"` = right-aligned blue, `role: "user"` = left-aligned grey).
- Parse the `transcript` field: if it's a JSON array of `{role, content}` objects, render as bubbles. If it's a plain string (legacy), render as a `<pre>` block.
- Show a **"Copy Transcript"** button that copies the full text to clipboard.

---

## Verification Plan

### Automated / Manual Verification
1. **Local Dev Test:** Run `npm run dev` in the frontend directory and `uvicorn app.main:app` in the backend directory. Test the entire end-to-end flow from UI Login -> UI Customer Creation -> Supabase verification -> Retell Dashboard verification.
2. **Deployment:** Deploy the Vite `dist` folder to Vercel/Netlify for free.
3. **Webhook Test:** Call the actual phone number linked to the Retell Agent. Verify the transcript appears automatically in the React SPA Call Logs tab!
