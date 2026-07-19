# Test Audio (agent editor web call) — design

2026-07-19. Follow-up to the Test LLM chat (PR #122): make the agent editor's
**Test Audio** tab place a real browser voice call to the agent.

## Goal

Clicking **Run Test** in the editor's Test Audio tab starts a live mic
conversation with the agent (its saved config), shows a live transcript in the
panel, and ends cleanly. The call is a first-class `web_call`: it is recorded,
analyzed, webhooked, and listed in call history like any phone call (matches
Retell).

Scope decided with the user: **call + live transcript**. Tool-invocation /
node-transition events in the panel are out of scope for this iteration.

## Current state

- `POST /v2/create-web-call` exists and mints a LiveKit room token
  (`access_token`) for room `call_{call_id}` — but never dispatches the agent
  worker, so nobody would answer.
- The worker registers with `agent_name="arhiteq-agent"` (explicit dispatch
  only) and its entrypoint waits for a **SIP** participant; a browser
  participant would time out as `dial_no_answer`. `_wait_for_answer` already
  treats non-SIP participants as answered.
- The dashboard's `AudioTab` is a disabled placeholder; no `livekit-client`
  dependency.
- Prod already exposes `wss://livekit.arhiteq.com` (managed cert + LB); the
  backend's own `LIVEKIT_URL` is the in-cluster address, so the
  browser-facing URL is separate config.

## Approach (chosen)

Native LiveKit end-to-end. Alternatives rejected:

- **Reuse `retell-client-js-sdk` in the dashboard** — the SDK hardcodes
  Retell's LiveKit endpoint; can't point at our infra without forking. (Note:
  this means external consumers using that SDK for *web* calls can't migrate
  as-is either; the migration-spec consumer is phone-only, so nothing blocks.)
- **Transcript via polling `get-call`** — laggy and redundant; livekit-agents
  already publishes real-time transcriptions to the `lk.transcription` text
  stream by default.

## Backend

`create_web_call` (`backend/src/arhiteq_api/api/calls.py`) additionally:

1. Dispatches the agent: new `telephony.dispatch_agent(call)` factored out of
   `start_outbound_call` (which now calls it too) — creates the LiveKit agent
   dispatch with metadata `{"call_id": ...}` so the worker joins the room and
   fetches config via the existing internal-API path. Dispatch failure →
   endpoint returns non-2xx (the call cannot work without it).
2. Returns the frozen Retell `V2WebCallResponse` shape **plus one extra
   field** (extra fields are contract-safe): the browser-reachable LiveKit
   URL, from new setting `ARHITEQ_PUBLIC_LIVEKIT_URL`, defaulting to
   `LIVEKIT_URL` so local dev (`ws://localhost:7880`) needs no new config.

Field name: `livekit_server_url`.

Operator note: the Helm chart already sets `ARHITEQ_PUBLIC_LIVEKIT_URL` in the api configmap.

Tests: contract tests keep asserting the frozen Retell fields; new tests
assert dispatch is created (mocked LiveKit API) and the extra field is
present, and that dispatch failure yields non-2xx.

## Worker

`worker/src/arhiteq_worker/main.py`:

1. **Participant wait**: when `cfg.call_type == "web_call"`, wait for any
   non-agent remote participant (new `_wait_for_web_participant`) instead of
   the SIP wait, bounded by `DIAL_TIMEOUT_S`. Browser never joins → call
   finalizes `dial_no_answer`, same as an unanswered dial. Downstream,
   `_wait_for_answer` already returns answered for non-SIP participants.
2. **Phone-only features gated off for web calls**: AMD/voicemail detection
   is skipped. `runtime.sip_participant_identity` is already `None` for
   non-SIP participants, so `end_call` falls back to room deletion (verify
   this path in tests). Call transfer remains unsupported in webcall (UI
   already says so).
3. **Transcript**: no work — `AgentSession` defaults publish user + agent
   transcriptions to the `lk.transcription` text stream.
4. **Recording, finalize, analysis, webhooks**: unchanged; web calls flow
   through the same lifecycle and appear in call history.

Tests (`uv run --only-group dev pytest`): web-participant wait selection,
AMD gate, web hang-up path.

## Frontend

- `frontend/src/lib/api.ts`: `createWebCall(agentId)` →
  `{ call_id, access_token, livekit_server_url, ... }`.
- New dependency: `livekit-client` (added with bun).
- `AudioTab` in `frontend/src/components/editor/TestPanel.tsx` (keep current
  visual language):
  - **Idle**: existing mic illustration; **Run Test** enabled.
  - **Start**: preflight mic permission (`getUserMedia`) so a denial aborts
    before any call is created → `createWebCall` → `new Room()` →
    `room.connect(livekit_server_url, access_token)` →
    `localParticipant.setMicrophoneEnabled(true)`.
  - **States**: connecting → in call (elapsed timer + speaking indicator from
    the agent's audio track) → ended (reset back to idle).
  - **Transcript**: `room.registerTextStreamHandler("lk.transcription", …)`;
    sender identity + `lk.transcribed_track_id` attributes distinguish agent
    vs user; render with the LLM tab's chat-bubble styles, replacing partial
    segments by segment id until final.
  - **End**: hang-up button → `room.disconnect()`; also end when the agent
    participant leaves (worker ended the call) or on unmount.
  - **Errors**: mic-permission denied, connect failure, and create-web-call
    failure use the LLM tab's inline error style.
- Mic capture needs a secure context — satisfied on `localhost` and prod
  HTTPS.

## Error handling summary

| Failure | Behavior |
| --- | --- |
| Dispatch fails at create | non-2xx from API; inline error in panel |
| Browser never joins | worker times out → `dial_no_answer` |
| Mic permission denied | inline error, call not started (no orphan call: permission is requested before `createWebCall`) |
| Worker dies mid-call | agent participant disconnect → UI ends call; backend finalize/timeout marks call terminal |
| User closes page | room disconnect; worker sees participant leave and ends the call |

## Testing

- Backend: pytest (contract + dispatch mocks).
- Worker: dev-group pytest for the new gates.
- Frontend: `bun run build`; manual verification via the local stack
  (`docker compose up -d`, `make api` / `make worker` / `make web`) — place a
  test call end-to-end.
