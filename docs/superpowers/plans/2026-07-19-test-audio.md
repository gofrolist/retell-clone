# Test Audio (agent editor web call) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent editor's Test Audio tab places a real browser voice call to the agent, with a live transcript in the panel.

**Architecture:** `POST /v2/create-web-call` gains an agent-job dispatch (so the worker joins the room) and returns the browser-reachable LiveKit URL as a contract-safe extra field. The worker learns to wait for a browser (STANDARD-kind) participant when `call_type == "web_call"`. The dashboard joins the room with `livekit-client`, publishes the mic, and renders the `lk.transcription` text stream.

**Tech Stack:** FastAPI + livekit (server SDK) backend · livekit-agents worker (Python 3.14, uv) · Next.js dashboard (bun) + `livekit-client`.

Spec: `docs/superpowers/specs/2026-07-19-test-audio-design.md`

## Global Constraints

- Wire contract is frozen: never rename/drop existing Retell response fields; extra fields are fine (`docs/RETELL_INTEGRATION_MAP.md`, `backend/tests/contract/`).
- Backend tests: `cd backend && uv run pytest`. Worker tests: `cd worker && uv run --only-group dev pytest` (worker `main.py` is NOT importable in the dev group — livekit-agents isn't installed there; don't write dev-group tests that import `arhiteq_worker.main`).
- Frontend: bun is the package manager; `cd frontend && bun run build` must pass.
- The Helm chart already sets `ARHITEQ_PUBLIC_LIVEKIT_URL` (api configmap) — no infra changes in this plan.
- Commit messages: conventional commits; pre-commit hooks (gitleaks, ruff, pytest, eslint) run on commit.
- Work happens on branch `feat/test-audio` (already created).

---

### Task 1: Backend — dispatch the agent for web calls + return `livekit_server_url`

**Files:**
- Modify: `backend/src/arhiteq_api/services/telephony.py`
- Modify: `backend/src/arhiteq_api/config.py` (after `sip_outbound_trunk_id`, ~line 35)
- Modify: `backend/src/arhiteq_api/api/calls.py:228-252` (`create_web_call`)
- Modify: `backend/tests/conftest.py:137-145` (`_stub_telephony`)
- Test: `backend/tests/contract/test_call_extras.py` (append to the `create-web-call` section)
- Modify: `docs/superpowers/specs/2026-07-19-test-audio-design.md` (env-var name fix, see Step 6)

**Interfaces:**
- Produces: `telephony.dispatch_agent(call: Call) -> None` (async; raises on LiveKit failure); `Settings.public_livekit_url: str` (env `ARHITEQ_PUBLIC_LIVEKIT_URL`, default `""`); create-web-call response gains `livekit_server_url: str`.
- Consumes: existing `telephony.room_name`, `AGENT_NAME`, `get_settings`.

- [ ] **Step 1: Write the failing contract tests**

Append to `backend/tests/contract/test_call_extras.py` (the file already defines `AUTH_HEADERS` and `AGENT_ID`; add `from arhiteq_api.config import get_settings` to its imports):

```python
async def test_create_web_call_dispatches_agent(client, monkeypatch):
    dispatched: list[str] = []

    async def _fake_dispatch(call):
        dispatched.append(call.call_id)

    monkeypatch.setattr("arhiteq_api.services.telephony.dispatch_agent", _fake_dispatch)
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 201
    assert dispatched == [resp.json()["call_id"]]


async def test_create_web_call_returns_livekit_server_url(client):
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 201
    # public_livekit_url is unset in tests, so the field falls back to livekit_url.
    assert resp.json()["livekit_server_url"] == get_settings().livekit_url


async def test_create_web_call_dispatch_failure_is_500(client, monkeypatch):
    async def _boom(call):
        raise RuntimeError("livekit down")

    monkeypatch.setattr("arhiteq_api.services.telephony.dispatch_agent", _boom)
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 500
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/contract/test_call_extras.py -v -k "dispatches_agent or livekit_server_url or dispatch_failure"`
Expected: 3 FAILs — the two monkeypatch tests with `AttributeError: ... has no attribute 'dispatch_agent'`, the URL test with `KeyError: 'livekit_server_url'`.

- [ ] **Step 3: Implement**

3a. `backend/src/arhiteq_api/config.py` — add after `sip_outbound_trunk_id`:

```python
    # Browser-reachable LiveKit URL (wss://livekit.<domain>), returned to web
    # call clients; the in-cluster livekit_url is not reachable from browsers.
    # Empty = fall back to livekit_url (right for local dev). The Helm chart
    # already sets ARHITEQ_PUBLIC_LIVEKIT_URL in the api configmap.
    public_livekit_url: str = ""
```

(The `env_prefix="ARHITEQ_"` makes the env var `ARHITEQ_PUBLIC_LIVEKIT_URL` — matching the chart. No alias needed.)

3b. `backend/src/arhiteq_api/services/telephony.py` — extract the dispatch from `start_outbound_call` into a reusable function; `start_outbound_call` now calls it:

```python
async def dispatch_agent(call: Call) -> None:
    """Create the agent job dispatch for the call's room.

    The worker (agent_name=AGENT_NAME) joins the room and fetches the call
    config through the internal API using the call_id in the metadata.
    Raises on failure — callers must not report the call as started.
    """
    settings = get_settings()
    from livekit import api as lk_api

    lk = lk_api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        await lk.agent_dispatch.create_dispatch(
            lk_api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name(call),
                metadata=json.dumps({"call_id": call.call_id}),
            )
        )
    finally:
        await lk.aclose()


async def start_outbound_call(call: Call) -> None:
    """Create the room, dispatch the agent job, and dial the callee.

    Raises on failure — create-phone-call must return non-2xx if the call
    could not be initiated (consumers mark the lead `retell_error` on non-2xx).
    """
    settings = get_settings()
    from livekit import api as lk_api

    await dispatch_agent(call)

    room = room_name(call)
    lk = lk_api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        await lk.sip.create_sip_participant(
            lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=settings.sip_outbound_trunk_id,
                sip_call_to=call.to_number,
                sip_number=call.from_number,
                room_name=room,
                participant_identity=f"pstn_{call.to_number}",
                wait_until_answered=False,
                headers=call.custom_sip_headers or {},
            )
        )
    finally:
        await lk.aclose()
    log.info("dialing %s -> %s in room %s", call.from_number, call.to_number, room)
```

3c. `backend/src/arhiteq_api/api/calls.py` — in `create_web_call`, replace the tail (`session.add(call)` / `await session.commit()` / `return web_call_to_dict(call)`) with:

```python
    session.add(call)
    await session.commit()

    try:
        await telephony.dispatch_agent(call)
    except Exception:
        log.exception("failed to dispatch agent for web call %s", call.call_id)
        call.call_status = "error"
        call.disconnection_reason = "error_telephony"
        await session.commit()
        raise HTTPException(500, detail="Failed to start web call agent")

    settings = get_settings()
    out = web_call_to_dict(call)
    # Arhiteq extra (contract-safe): where the browser should connect.
    out["livekit_server_url"] = settings.public_livekit_url or settings.livekit_url
    return out
```

(`get_settings`, `telephony`, and `log` are already imported in `calls.py`.)

3d. `backend/tests/conftest.py` — extend `_stub_telephony` so no contract test ever reaches LiveKit:

```python
@pytest.fixture(autouse=True)
def _stub_telephony(monkeypatch):
    """No LiveKit in tests: creating a call succeeds without dialing."""

    async def _noop(call):
        return None

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _noop)
    monkeypatch.setattr("arhiteq_api.services.telephony.dispatch_agent", _noop)
```

- [ ] **Step 4: Run the backend suite**

Run: `cd backend && uv run pytest`
Expected: all PASS (the three new tests plus the pre-existing web-call and phone-call contract tests).

- [ ] **Step 5: Sanity-check the settings env name**

Run: `cd backend && uv run python -c "import os; os.environ['ARHITEQ_PUBLIC_LIVEKIT_URL']='wss://livekit.example.com'; from arhiteq_api.config import Settings; print(Settings().public_livekit_url)"`
Expected output: `wss://livekit.example.com`

- [ ] **Step 6: Fix the spec's env-var name**

In `docs/superpowers/specs/2026-07-19-test-audio-design.md`, replace both occurrences of `ARHITEQ_LIVEKIT_PUBLIC_URL` with `ARHITEQ_PUBLIC_LIVEKIT_URL`, and change the operator note to say the Helm chart already sets it (no infra/private change needed).

- [ ] **Step 7: Commit**

```bash
git add backend/src/arhiteq_api/services/telephony.py backend/src/arhiteq_api/config.py backend/src/arhiteq_api/api/calls.py backend/tests/conftest.py backend/tests/contract/test_call_extras.py docs/superpowers/specs/2026-07-19-test-audio-design.md
git commit -m "feat(api): dispatch agent worker on create-web-call, return livekit_server_url"
```

---

### Task 2: Worker — answer web calls (wait for a browser participant)

**Files:**
- Modify: `worker/src/arhiteq_worker/main.py` (helpers near `_wait_for_sip_participant` at ~line 415; entrypoint wait at ~line 713)
- Test: `worker/tests/test_config.py`

**Interfaces:**
- Consumes: `CallConfig.call_type` (already parsed in `worker/src/arhiteq_worker/config.py`; the internal API sends `call_type="web_call"` and `direction="inbound"` for web calls).
- Produces: `_wait_for_web_participant(ctx, timeout)` used only inside `entrypoint`.

Background for the implementer:
- The dispatch created in Task 1 carries `{"call_id": ...}` metadata, so `_load_call_config` takes the metadata path and returns `(cfg, None)` — the entrypoint then waits for a participant. Today that wait is SIP-only; a browser joins as a STANDARD-kind participant, so web calls would time out as `dial_no_answer`.
- Everything downstream already handles web calls: `direction == "inbound"` marks the call answered as soon as a participant is present; `_amd_enabled` requires `direction == "outbound"` so AMD/voicemail never runs; `end_call` deletes the room (no SIP-specific hangup); `AgentSession` publishes transcriptions to the `lk.transcription` text stream by default.

- [ ] **Step 1: Write the failing dev-group test**

The worker's phone-vs-web gates all key off `call_type`/`direction` from the internal config; this locks the parse. Append to `worker/tests/test_config.py` (it already imports `CallConfig` and defines `CONFIG_DICT`):

```python
def test_web_call_config_parses_type_and_direction() -> None:
    cfg = CallConfig.from_dict(
        {**CONFIG_DICT, "call_type": "web_call", "direction": "inbound"}
    )
    assert cfg.call_type == "web_call"
    assert cfg.direction == "inbound"
    # Web calls resolve {{call_type}} like any call.
    assert cfg.resolution_variables()["call_type"] == "web_call"
```

- [ ] **Step 2: Run it**

Run: `cd worker && uv run --only-group dev pytest tests/test_config.py -v`
Expected: the new test PASSES already (parsing exists) — it's a regression lock, not new behavior. If it fails, stop: the internal-config assumption is wrong; re-read `worker/src/arhiteq_worker/config.py`.

- [ ] **Step 3: Add the web-participant wait to `main.py`**

Below `_wait_for_sip_participant` (~line 423) add:

```python
def _is_web_participant(p: rtc.RemoteParticipant) -> bool:
    """Browser callers join as STANDARD participants (agents/egress excluded)."""
    return getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD


async def _wait_for_web_participant(ctx: JobContext, timeout: float) -> rtc.RemoteParticipant:
    for p in ctx.room.remote_participants.values():
        if _is_web_participant(p):
            return p
    return await asyncio.wait_for(
        ctx.wait_for_participant(kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD),
        timeout,
    )
```

In `entrypoint`, replace the participant wait (~line 713):

```python
    if participant is None:
        try:
            if cfg.call_type == "web_call":
                # Web test call: the caller is a browser, not a SIP leg.
                participant = await _wait_for_web_participant(ctx, timeout=DIAL_TIMEOUT_S)
            else:
                participant = await _wait_for_sip_participant(ctx, timeout=DIAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            participant = None
```

- [ ] **Step 4: Verify the worker still imports and the dev suite passes**

Run: `cd worker && uv run --only-group dev pytest`
Expected: all PASS.

Run (full env so `main.py` actually compiles): `cd worker && uv sync && uv run python -c "import arhiteq_worker.main; print('ok')"`
Expected output: `ok`

- [ ] **Step 5: Commit**

```bash
git add worker/src/arhiteq_worker/main.py worker/tests/test_config.py
git commit -m "feat(worker): answer web calls by waiting for a browser participant"
```

---

### Task 3: Frontend — `api.createWebCall` + livekit-client dependency

**Files:**
- Modify: `frontend/src/lib/api.ts` (type near `RawChat` ~line 189; method in the "Test LLM (text chat)" block ~line 540)
- Modify: `frontend/package.json` (via `bun add`)

**Interfaces:**
- Produces: `api.createWebCall(agentId: string): Promise<RawWebCall>` and `interface RawWebCall { call_id; access_token; livekit_server_url; agent_id; call_status }` — Task 4 consumes both; `livekit-client` importable.
- Consumes: the Task 1 response shape; existing `request<T>` / `post` helpers (dashboard Bearer auth is built in).

- [ ] **Step 1: Add the dependency**

Run: `cd frontend && bun add livekit-client`
Expected: `package.json` gains `livekit-client` under dependencies; lockfile updated.

- [ ] **Step 2: Add the type and the API method**

In `frontend/src/lib/api.ts`, after the `RawChat` interface add:

```ts
export interface RawWebCall {
  call_id: string;
  access_token: string;
  /** Arhiteq extra: browser-reachable LiveKit signalling URL. */
  livekit_server_url: string;
  agent_id: string;
  call_status: string;
}
```

After `endChat` in the `api` object add:

```ts
  // --------------------------------------------------- Test Audio (web call)
  createWebCall: (agentId: string) =>
    request<RawWebCall>("/v2/create-web-call", post({ agent_id: agentId })),
```

- [ ] **Step 3: Build**

Run: `cd frontend && bun run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/bun.lock frontend/src/lib/api.ts
git commit -m "feat(dashboard): createWebCall API client + livekit-client dependency"
```

---

### Task 4: Frontend — working AudioTab (call lifecycle + live transcript)

**Files:**
- Modify: `frontend/src/components/editor/TestPanel.tsx` (replace the `AudioTab` component; pass `agentId` to it)

**Interfaces:**
- Consumes: `api.createWebCall` + `RawWebCall` (Task 3); `livekit-client` (`Room`, `RoomEvent`, `Track`); the backend + worker behavior from Tasks 1-2.
- Produces: user-facing feature; no exports.

Design notes (from the spec):
- Preflight mic permission before `createWebCall` so a denial never creates an orphan call.
- Transcript: `room.registerTextStreamHandler("lk.transcription", …)` — register **before** `room.connect` so the agent's greeting isn't missed. Segment role: if `lk.transcribed_track_id` matches a local audio publication (or the sender identity is the local participant), it's the user; otherwise the agent. Replace segments by id as partials finalize.
- Reuse the LLM tab's chat-bubble classes verbatim so the two transcripts look identical.
- The call ends when: the user clicks hang-up, the agent participant leaves (worker ended the call), or the panel unmounts.

- [ ] **Step 1: Update imports and the `TestPanel` wiring**

In `frontend/src/components/editor/TestPanel.tsx`:

```tsx
import { PillTabs } from "@/components/ui/Tabs";
import { api, type ChatMessage } from "@/lib/api";
import { Room, RoomEvent, Track } from "livekit-client";
import { Braces, Info, Loader2, Mic, Phone, Play, RotateCcw, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
```

In `TestPanel`, pass the agent id to the audio tab:

```tsx
      <div className={tab === "audio" ? "flex min-h-0 grow flex-col" : "hidden"}>
        <AudioTab agentId={agentId} />
      </div>
```

- [ ] **Step 2: Replace the `AudioTab` component**

```tsx
type CallPhase = "idle" | "connecting" | "active" | "ended";

interface TranscriptSegment {
  id: string;
  role: "agent" | "user";
  text: string;
}

/** Live web call against the agent (Retell "Test Audio"). */
function AudioTab({ agentId }: { agentId: string }) {
  const [phase, setPhase] = useState<CallPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const audioRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the newest transcript line in view.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [segments]);

  // Call timer.
  useEffect(() => {
    if (phase !== "active") return;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [phase]);

  // Hang up when the panel goes away (page navigation).
  useEffect(() => () => void roomRef.current?.disconnect(), []);

  const hangUp = () => void roomRef.current?.disconnect();

  const start = async () => {
    setError(null);
    setSegments([]);
    setElapsed(0);
    // Preflight mic permission: a denial must abort before any call exists.
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      mic.getTracks().forEach((t) => t.stop());
    } catch {
      setError("Microphone access is blocked — allow it in the browser and retry.");
      return;
    }
    setPhase("connecting");
    const room = new Room();
    roomRef.current = room;
    try {
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) audioRef.current?.appendChild(track.attach());
      });
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        setAgentSpeaking(speakers.some((p) => p.identity !== room.localParticipant.identity));
      });
      // The worker left (it ended the call server-side): leave too.
      room.on(RoomEvent.ParticipantDisconnected, () => void room.disconnect());
      room.on(RoomEvent.Disconnected, () => {
        roomRef.current = null;
        setAgentSpeaking(false);
        setPhase((p) => (p === "idle" ? p : "ended"));
      });
      // Register before connect so the agent's greeting is never missed.
      room.registerTextStreamHandler("lk.transcription", (reader, participantInfo) => {
        void (async () => {
          const text = await reader.readAll();
          if (!text) return;
          const attrs = reader.info.attributes ?? {};
          const id = attrs["lk.segment_id"] ?? reader.info.id;
          const trackId = attrs["lk.transcribed_track_id"] ?? "";
          const isUser =
            room.localParticipant.audioTrackPublications.has(trackId) ||
            participantInfo.identity === room.localParticipant.identity;
          setSegments((prev) => {
            const seg: TranscriptSegment = { id, role: isUser ? "user" : "agent", text };
            const i = prev.findIndex((s) => s.id === id);
            if (i < 0) return [...prev, seg];
            const next = prev.slice();
            next[i] = seg;
            return next;
          });
        })();
      });
      const call = await api.createWebCall(agentId);
      await room.connect(call.livekit_server_url, call.access_token);
      await room.localParticipant.setMicrophoneEnabled(true);
      setPhase("active");
    } catch (e) {
      roomRef.current = null;
      void room.disconnect();
      setPhase("idle");
      setError(e instanceof Error ? e.message : "Failed to start the test call");
    }
  };

  const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const secs = String(elapsed % 60).padStart(2, "0");
  const inCall = phase === "connecting" || phase === "active";

  return (
    <>
      {/* Hidden sink the agent's audio elements attach into. */}
      <div ref={audioRef} className="hidden" />
      {segments.length > 0 || phase === "active" ? (
        <div ref={scrollRef} className="min-h-0 grow space-y-3 overflow-y-auto px-4 py-4">
          {segments.map((m) => (
            <div
              key={m.id}
              className={
                m.role === "user"
                  ? "ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-[13px] text-white"
                  : "mr-auto max-w-[85%] rounded-2xl rounded-bl-sm border border-line bg-app px-3.5 py-2 text-[13px] text-ink whitespace-pre-wrap"
              }
            >
              {m.text}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex grow flex-col items-center justify-center gap-6 px-6">
          <div className="relative flex size-24 items-center justify-center">
            <span
              className={`absolute inset-0 rounded-full bg-app ${agentSpeaking ? "animate-pulse" : ""}`}
            />
            <span className="absolute inset-3 rounded-full border border-line bg-white shadow-sm" />
            <Mic className="relative size-8 text-line-strong" strokeWidth={1.5} />
          </div>
          {phase === "ended" && <p className="text-xs text-sub">Call ended.</p>}
        </div>
      )}
      <div className="space-y-3 border-t border-line px-6 py-4">
        {error && <p className="px-1 text-center text-xs text-bad">{error}</p>}
        {!inCall && (
          <p className="flex items-center justify-center gap-1.5 text-center text-xs text-sub">
            <Info className="size-3.5 shrink-0" />
            Please note call transfer is not supported in Webcall.
          </p>
        )}
        <div className="flex items-center justify-center gap-3">
          {inCall ? (
            <>
              <span className="text-xs tabular-nums text-sub">
                {phase === "connecting" ? "Connecting…" : `${mins}:${secs}`}
              </span>
              <button
                onClick={hangUp}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-bad px-5 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-bad/85 cursor-pointer"
              >
                <Phone className="size-3.5" />
                End Call
              </button>
            </>
          ) : (
            <button
              onClick={() => void start()}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-5 text-[13px] font-medium shadow-sm transition-colors hover:bg-app cursor-pointer"
            >
              {phase === "ended" ? <RotateCcw className="size-3.5" /> : <Play className="size-3.5" />}
              {phase === "ended" ? "Run Again" : "Run Test"}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
```

Note: when a call ends, `segments` stays rendered (the user can read the transcript); "Run Again" clears it via `start()`.

- [ ] **Step 3: Build and lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: both pass. If `bun run lint` is not a defined script, rely on the pre-commit eslint hook in Step 4.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/editor/TestPanel.tsx
git commit -m "feat(dashboard): working Test Audio web call with live transcript"
```

---

### Task 5: End-to-end verification on the local stack

**Files:** none (verification only)

**Interfaces:** consumes everything above.

- [ ] **Step 1: Start the stack**

Use the project `/verify` skill if available in your session; otherwise:

```bash
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
docker compose up -d
make api      # terminal 1
make worker   # terminal 2 (needs livekit-agents: cd worker && uv sync first)
make web      # terminal 3
```

- [ ] **Step 2: Place a test call**

In the dashboard (`http://localhost:3000`), open an agent → Test Audio tab → Run Test. Grant mic permission.

Expected:
- Status goes Connecting… → timer.
- The agent speaks (audio audible) and its words appear as transcript bubbles; your replies appear as accent-colored bubbles.
- End Call disconnects; the worker finalizes the call (check `make api` logs for the finalize) and the call appears in the dashboard's call history as a `web_call` with transcript/analysis.

- [ ] **Step 3: Failure paths**

- Deny mic permission (site settings) → inline error, and no new call row is created.
- Stop the worker (`Ctrl-C` in terminal 2), Run Test → the call connects to the room but no agent joins; verify the UI can still End Call cleanly. (The backend dispatch succeeds — LiveKit queues the job — so this surfaces as silence, not an error; acceptable.)

- [ ] **Step 4: Record any deviations**

If verification exposed fixes, commit them as `fix:` commits on this branch before opening the PR.

---

## Self-review (done at plan-writing time)

- **Spec coverage:** backend dispatch + extra field (Task 1), env-name correction (Task 1 Step 6), worker web-participant wait + phone-gate verification (Task 2), frontend client + AudioTab with preflight/transcript/end-paths (Tasks 3-4), error-handling table exercised in Task 5 Step 3. AMD gating and `end_call` room-deletion needed no code change (verified in repo: `_amd_enabled` requires `direction == "outbound"`; `end_call` deletes the room unconditionally) — locked indirectly by the Task 2 config test.
- **Placeholders:** none; all code inline.
- **Type consistency:** `dispatch_agent(call)` name matches across telephony/calls/conftest/tests; `RawWebCall.livekit_server_url` matches the backend field; `AudioTab({ agentId })` matches the `TestPanel` call site.
