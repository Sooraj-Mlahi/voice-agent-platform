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
1. **Customers List (`/customers`):**
   * Calls `GET /api/customers`.
   * Displays a Tailwind Table of all customers.
   * Includes a "Create New Customer" button that opens a Modal.
2. **Customer Creation Modal:**
   * A form to gather `name`, `billing_email`, `phone_number`, etc.
   * Submits `POST /api/customers` to instantly wire up the agent in Retell and Postgres.
3. **Agent Config Editor (`/customers/:id/config`):**
   * A dedicated settings page fetched via the `customer_id`.
   * Includes `<textarea>` for the `system_prompt` and dropdowns for `voice_id` and `llm_model`.
   * Submits `PUT /api/customers/{id}/config` to update Retell in real-time.
4. **Call Logs (`/calls`):**
   * Calls `GET /api/calls`.
   * Displays recent incoming calls and allows the user to expand rows to read the `transcript` created by the Webhook.

---

## Verification Plan

### Automated / Manual Verification
1. **Local Dev Test:** Run `npm run dev` in the frontend directory and `uvicorn app.main:app` in the backend directory. Test the entire end-to-end flow from UI Login -> UI Customer Creation -> Supabase verification -> Retell Dashboard verification.
2. **Deployment:** Deploy the Vite `dist` folder to Vercel/Netlify for free.
3. **Webhook Test:** Call the actual phone number linked to the Retell Agent. Verify the transcript appears automatically in the React SPA Call Logs tab!
