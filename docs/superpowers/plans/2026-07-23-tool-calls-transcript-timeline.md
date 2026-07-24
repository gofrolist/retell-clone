# Tool Call Transcript Blocks + Audio Timeline Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Retell-style collapsible "Tool Invocation" / "Tool Result" blocks in the call-history transcription view, and show tool-call / KB-retrieval markers with hover popups (click-to-seek) on the audio player timeline.

**Architecture:** The worker already records tool events in `CallState.items` → `transcript_with_tool_calls`, and the backend persists/serves it untyped (`list[Any]`), so backend needs zero changes. Worker gains per-item `time_ms` (offset from answer) and `tool_call_id` stamps — additive fields. Frontend gains a `TranscriptItem` type fed from `transcript_with_tool_calls` (falling back to `transcript_object` for old calls), new render branches in `Transcript.tsx`, and a `markers` prop on `AudioPlayer.tsx` using the existing `HoverCard` portal popover.

**Tech Stack:** Python 3.14 + uv (worker), Next.js + TypeScript + Tailwind + bun (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-23-tool-calls-transcript-timeline-design.md`

## Global Constraints

- Retell wire contract is frozen: never rename/remove existing fields on `transcript_with_tool_calls` items (`role`, `name`, `arguments`, `content`). New fields `time_ms` / `tool_call_id` are additive only.
- Worker tests run with `cd worker && uv run --only-group dev pytest` (dev group only — the livekit stack is not installed).
- Frontend package manager is bun; verification is `cd frontend && bun run build`.
- Branch: `feat/call-tool-blocks-timeline` (already exists, spec committed). Commit after every task.
- Do not touch `backend/` — no changes required there.
- The existing worker item shapes and the `transcript_object()` filter (`role in ("agent","user")`) must keep passing `worker/tests/test_state.py::TestTranscript` unchanged.

---

### Task 1: Worker — stamp `time_ms` and `tool_call_id` on CallState items

**Files:**
- Modify: `worker/src/arhiteq_worker/state.py:41-49` (the three `add_*` methods + one new field/helper)
- Modify: `docs/INTERNAL_API.md:75` (document the additive fields)
- Test: `worker/tests/test_state.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: every item appended to `CallState.items` carries `time_ms: int` (ms since `answered_at_ms`, clamped ≥ 0; **omitted** when `answered_at_ms` is None). `add_tool_invocation` items carry `tool_call_id: str` (`"tool_call_1"`, `"tool_call_2"`, …). `add_tool_result` items carry the `tool_call_id` of the most recent same-name invocation that has no result yet (omitted if none matches). Callers in `tools.py` are unchanged. These fields flow into `build_finalize_payload()["transcript_with_tool_calls"]` (and `transcript_object`) automatically.

- [ ] **Step 1: Write the failing tests**

Append to `worker/tests/test_state.py`:

```python
class TestItemTimingAndToolIds:
    def test_time_ms_is_offset_from_answer(self, monkeypatch):
        s = CallState(call_id="c")
        s.answered_at_ms = 1_000_000
        monkeypatch.setattr("arhiteq_worker.state.now_ms", lambda: 1_012_345)
        s.add_message("agent", "Hi")
        s.add_tool_invocation("log_outcome", "{}")
        s.add_tool_result("log_outcome", "ok")
        assert [i["time_ms"] for i in s.items] == [12_345, 12_345, 12_345]

    def test_unanswered_items_have_no_time_ms(self):
        s = CallState()
        s.add_message("agent", "Hi")
        s.add_tool_invocation("t", "{}")
        assert all("time_ms" not in i for i in s.items)

    def test_clock_skew_clamps_to_zero(self, monkeypatch):
        s = CallState()
        s.answered_at_ms = 2_000_000
        monkeypatch.setattr("arhiteq_worker.state.now_ms", lambda: 1_999_000)
        s.add_message("user", "Hi")
        assert s.items[0]["time_ms"] == 0

    def test_tool_call_id_pairs_result_with_invocation(self):
        s = CallState()
        s.add_tool_invocation("a", "{}")
        s.add_tool_invocation("b", "{}")
        s.add_tool_result("b", "rb")
        s.add_tool_result("a", "ra")
        inv_a, inv_b, res_b, res_a = s.items
        assert inv_a["tool_call_id"] != inv_b["tool_call_id"]
        assert res_b["tool_call_id"] == inv_b["tool_call_id"]
        assert res_a["tool_call_id"] == inv_a["tool_call_id"]

    def test_repeated_same_tool_pairs_in_order(self):
        s = CallState()
        s.add_tool_invocation("t", "{}")
        s.add_tool_result("t", "r1")
        s.add_tool_invocation("t", "{}")
        s.add_tool_result("t", "r2")
        assert s.items[1]["tool_call_id"] == s.items[0]["tool_call_id"]
        assert s.items[3]["tool_call_id"] == s.items[2]["tool_call_id"]

    def test_result_without_invocation_has_no_tool_call_id(self):
        s = CallState()
        s.add_tool_result("orphan", "r")
        assert "tool_call_id" not in s.items[0]

    def test_finalize_payload_items_carry_new_fields(self, monkeypatch):
        s = CallState(call_id="c")
        s.answered_at_ms = 1_000_000
        s.ended_at_ms = 1_060_000
        monkeypatch.setattr("arhiteq_worker.state.now_ms", lambda: 1_030_000)
        s.add_tool_invocation("log_outcome", '{"k": 1}')
        s.add_tool_result("log_outcome", "ok")
        items = s.build_finalize_payload()["transcript_with_tool_calls"]
        assert items[0]["time_ms"] == 30_000
        assert items[0]["tool_call_id"] == items[1]["tool_call_id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd worker && uv run --only-group dev pytest tests/test_state.py -v`
Expected: the 7 new `TestItemTimingAndToolIds` tests FAIL with `KeyError: 'time_ms'` / `KeyError: 'tool_call_id'`; all pre-existing tests PASS.

- [ ] **Step 3: Implement stamping in `CallState`**

In `worker/src/arhiteq_worker/state.py`, add a field after `collected_dynamic_variables` (line 39) and replace the three `add_*` methods (lines 41-49):

```python
    # Monotone counter behind generated tool_call_id values.
    tool_seq: int = field(default=0, init=False)

    def _stamp(self, item: dict[str, Any]) -> dict[str, Any]:
        # time_ms = offset from answer ≈ offset into the recording; items
        # logged before answer (none today) simply carry no timestamp.
        if self.answered_at_ms is not None:
            item["time_ms"] = max(0, now_ms() - self.answered_at_ms)
        return item

    def add_message(self, role: str, content: str) -> None:
        if content:
            self.items.append(self._stamp({"role": role, "content": content}))

    def add_tool_invocation(self, name: str, arguments: str) -> None:
        self.tool_seq += 1
        self.items.append(
            self._stamp(
                {
                    "role": "tool_call_invocation",
                    "name": name,
                    "arguments": arguments,
                    "tool_call_id": f"tool_call_{self.tool_seq}",
                }
            )
        )

    def add_tool_result(self, name: str, content: str) -> None:
        item: dict[str, Any] = {"role": "tool_call_result", "name": name, "content": content}
        tool_call_id = self._pending_tool_call_id(name)
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        self.items.append(self._stamp(item))

    def _pending_tool_call_id(self, name: str) -> str | None:
        """tool_call_id of the newest same-name invocation with no result yet."""
        matched = {
            i.get("tool_call_id") for i in self.items if i.get("role") == "tool_call_result"
        }
        for item in reversed(self.items):
            if item.get("role") == "tool_call_invocation" and item.get("name") == name:
                tool_call_id = item.get("tool_call_id")
                if tool_call_id not in matched:
                    return tool_call_id
        return None
```

Note: `CallState` is `@dataclass(slots=True)`; `field(default=0, init=False)` works with slots. `field` is already imported.

- [ ] **Step 4: Run the worker test suite**

Run: `cd worker && uv run --only-group dev pytest -v`
Expected: ALL tests PASS — including the untouched `TestTranscript` (unanswered states get no `time_ms`, so the old dict-equality assertions still hold) and `tests/test_tools.py` (it asserts roles/names, not exact dicts). If any `test_tools.py` test compares an item dict exactly, update it to include the new keys — but do not weaken role/name/arguments assertions.

- [ ] **Step 5: Document the additive fields in the internal API doc**

In `docs/INTERNAL_API.md`, replace line 75:

```jsonc
  "transcript_object": [...], "transcript_with_tool_calls": [...],
```

with:

```jsonc
  "transcript_object": [...], "transcript_with_tool_calls": [...],
      // items additionally carry "time_ms" (offset from answer, ms) and — on
      // tool_call_invocation/tool_call_result — "tool_call_id" pairing them
```

- [ ] **Step 6: Commit**

```bash
git add worker/src/arhiteq_worker/state.py worker/tests/test_state.py docs/INTERNAL_API.md
git commit -m "feat(worker): stamp time_ms and tool_call_id on transcript items"
```

---

### Task 2: Frontend — `TranscriptItem` type and adapter plumbing

**Files:**
- Modify: `frontend/src/lib/types.ts:73-77, 100`
- Modify: `frontend/src/lib/api.ts:312-341` (RawCall) and `:468-510` (adapter)
- Modify: `frontend/src/lib/mock.ts:14, 216-228` (rename + demo tool items)
- Modify: `frontend/src/components/calls/Transcript.tsx:3, 6` (type rename only — visual changes are Task 3)

**Interfaces:**
- Consumes: worker fields from Task 1 (`time_ms`, `tool_call_id` on raw items) — but must also work when they are absent (old calls).
- Produces: `TranscriptItem` in `types.ts` (exact shape below); `Call.transcript` becomes `TranscriptItem[]`; `uiCallFromRaw` fills it from `transcript_with_tool_calls` when non-empty, else `transcript_object`. Task 3 and 4 consume `TranscriptItem` and its `role` values `"tool_invocation"` / `"tool_result"` (UI-side names; the raw wire roles remain `tool_call_invocation` / `tool_call_result`).

- [ ] **Step 1: Replace `TranscriptTurn` with `TranscriptItem` in `types.ts`**

Replace lines 73-77:

```ts
export interface TranscriptTurn {
  role: "agent" | "user" | "kb_retrieval";
  content: string;
  time: string; // "0:03"
}
```

with:

```ts
export interface TranscriptItem {
  role: "agent" | "user" | "kb_retrieval" | "tool_invocation" | "tool_result";
  /** Utterance text, tool arguments JSON (invocation), or tool result body. */
  content: string;
  /** Tool name — tool_invocation / tool_result only. */
  name?: string;
  /** Pairs a tool_result with its tool_invocation. */
  tool_call_id?: string;
  /** Offset from call start in ms; drives audio-timeline markers. */
  time_ms?: number;
  time: string; // "0:03", "" when unknown
}
```

And change line 100 `transcript?: TranscriptTurn[];` → `transcript?: TranscriptItem[];`.

- [ ] **Step 2: Update `RawCall` and the adapter in `api.ts`**

Replace line 327:

```ts
  transcript_object?: { role: string; content: string; words?: unknown[] }[];
```

with (and add the sibling field):

```ts
  transcript_object?: RawTranscriptItem[];
  transcript_with_tool_calls?: RawTranscriptItem[];
```

Above `export interface RawCall` add:

```ts
/** Item of transcript_object / transcript_with_tool_calls as served by the
 *  API (worker-recorded). time_ms / tool_call_id exist only on calls recorded
 *  after the worker started stamping them. */
export interface RawTranscriptItem {
  role: string;
  content?: string;
  name?: string;
  arguments?: string;
  tool_call_id?: string;
  time_ms?: number;
  words?: unknown[];
}
```

In the adapters section, add a helper above `uiCallFromRaw` (after `const SENTIMENTS…`, line 466):

```ts
function transcriptFromRaw(c: RawCall): TranscriptItem[] {
  // Prefer the tool-bearing stream; old calls only have transcript_object.
  const source = c.transcript_with_tool_calls?.length
    ? c.transcript_with_tool_calls
    : (c.transcript_object ?? []);
  return source.map((t) => {
    const time_ms = typeof t.time_ms === "number" ? t.time_ms : undefined;
    const base = { time_ms, time: time_ms !== undefined ? formatDuration(time_ms) : "" };
    if (t.role === "tool_call_invocation") {
      return {
        role: "tool_invocation" as const,
        name: t.name,
        tool_call_id: t.tool_call_id,
        content: t.arguments ?? "",
        ...base,
      };
    }
    if (t.role === "tool_call_result") {
      return {
        role: "tool_result" as const,
        name: t.name,
        tool_call_id: t.tool_call_id,
        content: t.content ?? "",
        ...base,
      };
    }
    return {
      role: t.role === "agent" ? ("agent" as const) : t.role === "kb_retrieval" ? ("kb_retrieval" as const) : ("user" as const),
      content: t.content ?? "",
      ...base,
    };
  });
}
```

In `uiCallFromRaw`, replace the `transcript:` mapping (lines 502-506):

```ts
    transcript: (c.transcript_object ?? []).map((t) => ({
      role: t.role === "agent" ? "agent" : t.role === "kb_retrieval" ? "kb_retrieval" : "user",
      content: t.content,
      time: "",
    })),
```

with:

```ts
    transcript: transcriptFromRaw(c),
```

Update imports: `formatDuration` comes from `@/lib/utils` (`import { formatDuration } from "./utils";` — check the top of `api.ts` for the existing import style and extend/add accordingly), and the type import list must now include `TranscriptItem` from `./types` (replace `TranscriptTurn` if imported).

- [ ] **Step 3: Fix the two remaining `TranscriptTurn` references**

- `frontend/src/lib/mock.ts:14` — rename the imported type to `TranscriptItem`; line 216 `const TRANSCRIPT: TranscriptTurn[]` → `const TRANSCRIPT: TranscriptItem[]`. Also append demo tool items after the `kb_retrieval` entry (line ~221) so mock mode exercises the new UI:

```ts
  { role: "tool_invocation", name: "log_outcome", tool_call_id: "tool_call_1", content: '{"call_type": "morning_checkin", "outcome": "completed"}', time: "0:41", time_ms: 41_000 },
  { role: "tool_result", name: "log_outcome", tool_call_id: "tool_call_1", content: '{"success": true}', time: "0:41", time_ms: 41_000 },
```

Also add `time_ms: 20_000` to the existing `kb_retrieval` mock entry.

- `frontend/src/components/calls/Transcript.tsx:3,6` — change the import and prop type to `TranscriptItem` (`turns: TranscriptItem[]`). No rendering changes yet — the new roles simply fall through to the existing user/agent branch until Task 3.

- [ ] **Step 4: Verify the build**

Run: `cd frontend && bun run build`
Expected: build succeeds with no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/mock.ts frontend/src/components/calls/Transcript.tsx
git commit -m "feat(dashboard): plumb transcript_with_tool_calls into the call transcript type"
```

---

### Task 3: Transcript UI — collapsible Tool Invocation / Tool Result blocks

**Files:**
- Modify: `frontend/src/components/calls/Transcript.tsx` (full replacement below)

**Interfaces:**
- Consumes: `TranscriptItem` from Task 2 (`role: "tool_invocation" | "tool_result"`, `name`, `tool_call_id`, `content`, `time`).
- Produces: unchanged component API — `<Transcript turns={items} />` where `turns: TranscriptItem[]` (CallDrawer's call site already compiles from Task 2).

- [ ] **Step 1: Replace `Transcript.tsx` with the tool-aware version**

```tsx
"use client";

import type { TranscriptItem } from "@/lib/types";
import { ChevronDown, ChevronRight, Library } from "lucide-react";
import { useState } from "react";

/** Pretty-print JSON payloads; non-JSON content renders verbatim. */
function prettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

/** Retell-style collapsible block for a tool invocation or result. */
function ToolBlock({ item }: { item: TranscriptItem }) {
  const [open, setOpen] = useState(true);
  const title =
    item.role === "tool_invocation"
      ? `Tool Invocation${item.name ? `: ${item.name}` : ""}`
      : "Tool Result";
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 text-[13px] font-medium text-accent-deep cursor-pointer"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        {title}
        <span className="ml-auto text-[11px] font-normal text-faint">{item.time}</span>
      </button>
      {open && (
        <div className="mt-1.5 rounded-lg border border-line bg-app/50 px-3 py-2 font-mono text-[12px] leading-relaxed">
          {item.tool_call_id && (
            <div className="mb-1 text-sub">tool_call_id: {item.tool_call_id}</div>
          )}
          <pre className="whitespace-pre-wrap break-words">{prettyJson(item.content)}</pre>
        </div>
      )}
    </div>
  );
}

export default function Transcript({ turns }: { turns: TranscriptItem[] }) {
  if (!turns.length) {
    return <p className="py-8 text-center text-[13px] text-sub">No transcript available.</p>;
  }
  return (
    <div className="space-y-3">
      {turns.map((t, i) =>
        t.role === "tool_invocation" || t.role === "tool_result" ? (
          <ToolBlock key={i} item={t} />
        ) : t.role === "kb_retrieval" ? (
          <div key={i} className="flex items-center gap-2 py-0.5">
            <div className="h-px grow bg-line" />
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-app px-2.5 py-0.5 text-[11.5px] font-medium text-sub">
              <Library className="size-3" />
              Knowledge Base Retrieval
            </span>
            <div className="h-px grow bg-line" />
          </div>
        ) : (
          <div key={i} className={t.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div className="max-w-[78%]">
              <div
                className={
                  t.role === "user"
                    ? "rounded-2xl rounded-br-sm bg-ink px-3.5 py-2 text-[13px] text-white"
                    : "rounded-2xl rounded-bl-sm border border-line bg-app px-3.5 py-2 text-[13px]"
                }
              >
                {t.content}
              </div>
              <div
                className={`mt-0.5 text-[11px] text-faint ${t.role === "user" ? "text-right" : ""}`}
              >
                {t.role === "user" ? "User" : "Agent"}
                {t.time ? ` · ${t.time}` : ""}
              </div>
            </div>
          </div>
        ),
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the build and eyeball mock mode**

Run: `cd frontend && bun run build`
Expected: build succeeds. (The mock transcript from Task 2 now renders one invocation + result block pair — if you have the dev stack running, check a mock call's drawer.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/calls/Transcript.tsx
git commit -m "feat(dashboard): render tool invocation/result blocks in call transcript"
```

---

### Task 4: Audio timeline markers with popup annotations

**Files:**
- Modify: `frontend/src/components/calls/AudioPlayer.tsx` (full replacement below)
- Modify: `frontend/src/components/calls/CallDrawer.tsx:3, 233-237` (build + pass markers)

**Interfaces:**
- Consumes: `TranscriptItem[]` on `Call.transcript` (Task 2), `HoverCard` from `@/components/ui/HoverCard`.
- Produces: `AudioMarker` exported from `AudioPlayer.tsx`: `{ time_ms: number; kind: "tool" | "kb"; title: string; body?: string }`; `AudioPlayer` accepts optional `markers?: AudioMarker[]`.

- [ ] **Step 1: Replace `AudioPlayer.tsx`**

```tsx
"use client";

import HoverCard from "@/components/ui/HoverCard";
import { formatDuration, isHttpUrl } from "@/lib/utils";
import { Download, Play, Pause } from "lucide-react";
import { useRef, useState } from "react";

/** Timeline annotation (tool call / KB retrieval) shown as a dot on the bar. */
export interface AudioMarker {
  time_ms: number;
  kind: "tool" | "kb";
  title: string;
  body?: string;
}

/** Audio player for call recordings, matching the call drawer design. */
export default function AudioPlayer({
  src,
  durationMs = 0,
  markers,
}: {
  src: string;
  durationMs?: number;
  markers?: AudioMarker[];
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentSec, setCurrentSec] = useState(0);
  const [durationSec, setDurationSec] = useState(durationMs / 1000);

  const progress = durationSec > 0 ? Math.min(1, currentSec / durationSec) : 0;
  // Only trust http(s) recording URLs — never render javascript:/data: schemes.
  const safeSrc = src && isHttpUrl(src) ? src : undefined;
  // Items timed past the recording end would render off-bar — drop them.
  const visibleMarkers =
    durationSec > 0
      ? (markers ?? []).filter((m) => m.time_ms >= 0 && m.time_ms <= durationSec * 1000)
      : [];

  function toggle() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play();
    else audio.pause();
  }

  function seekTo(sec: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = sec;
    setCurrentSec(sec);
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const bar = barRef.current;
    if (!bar || !durationSec) return;
    const rect = bar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    seekTo(ratio * durationSec);
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-white px-3 py-2.5 shadow-sm">
      <audio
        ref={audioRef}
        src={safeSrc}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setCurrentSec(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          if (Number.isFinite(e.currentTarget.duration)) {
            setDurationSec(e.currentTarget.duration);
          }
        }}
      />
      <button
        onClick={toggle}
        className="flex size-8 items-center justify-center rounded-full bg-ink text-white hover:bg-black/80 cursor-pointer shrink-0"
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? <Pause className="size-3.5" /> : <Play className="ml-0.5 size-3.5" />}
      </button>
      <span className="text-xs tabular-nums text-sub shrink-0">
        {formatDuration(currentSec * 1000)}
      </span>
      <div
        ref={barRef}
        onClick={seek}
        className="relative h-1 grow cursor-pointer rounded-full bg-line"
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-ink"
          style={{ width: `${progress * 100}%` }}
        />
        <div
          className="absolute top-1/2 size-3 -translate-y-1/2 rounded-full border border-line-strong bg-white shadow"
          style={{ left: `calc(${progress * 100}% - 6px)` }}
        />
        {visibleMarkers.map((m, i) => (
          <span
            key={i}
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${(m.time_ms / (durationSec * 1000)) * 100}%` }}
          >
            <HoverCard
              trigger={
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    seekTo(m.time_ms / 1000);
                  }}
                  aria-label={m.title}
                  className={`block size-2.5 rounded-full border-2 border-white shadow cursor-pointer ${
                    m.kind === "tool" ? "bg-accent-deep" : "bg-sub"
                  }`}
                />
              }
            >
              <div className="text-[12px]">
                <div className="mb-0.5 flex items-center justify-between gap-2">
                  <span className="font-medium">{m.title}</span>
                  <span className="tabular-nums text-faint">{formatDuration(m.time_ms)}</span>
                </div>
                {m.body && (
                  <pre className="max-h-40 overflow-hidden font-mono text-[11px] whitespace-pre-wrap break-words text-sub">
                    {m.body}
                  </pre>
                )}
              </div>
            </HoverCard>
          </span>
        ))}
      </div>
      <span className="text-xs tabular-nums text-sub shrink-0">
        {formatDuration(durationSec * 1000)}
      </span>
      {safeSrc && (
        <a
          href={safeSrc}
          download
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-md p-1.5 text-faint hover:bg-app hover:text-ink cursor-pointer shrink-0"
          aria-label="Download recording"
        >
          <Download className="size-4" />
        </a>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Build markers in `CallDrawer.tsx`**

Change the import on line 3 to also pull the type:

```ts
import AudioPlayer, { type AudioMarker } from "./AudioPlayer";
```

Inside the component, right after `const c = full ?? call;` (line 140), add:

```ts
  // Timeline annotations: one dot per tool invocation (popup includes its
  // paired result) and per KB retrieval. Items without time_ms (calls
  // recorded before the worker stamped timestamps) get no marker.
  const items = c.transcript ?? [];
  const markers: AudioMarker[] = items.flatMap((t, i) => {
    if (typeof t.time_ms !== "number") return [];
    if (t.role === "tool_invocation") {
      const result = items.find(
        (r, j) =>
          j > i &&
          r.role === "tool_result" &&
          (t.tool_call_id ? r.tool_call_id === t.tool_call_id : r.name === t.name),
      );
      const body = [`args: ${t.content}`, result ? `result: ${result.content}` : null]
        .filter(Boolean)
        .join("\n");
      return [{ time_ms: t.time_ms, kind: "tool" as const, title: t.name ?? "tool call", body }];
    }
    if (t.role === "kb_retrieval") {
      return [{ time_ms: t.time_ms, kind: "kb" as const, title: "Knowledge Base Retrieval" }];
    }
    return [];
  });
```

And pass them to the player (lines 233-237):

```tsx
            {c.recording_url && /^https?:/i.test(c.recording_url) && (
              <div className="mt-4">
                <AudioPlayer
                  src={c.recording_url}
                  durationMs={c.duration_ms || 0}
                  markers={markers}
                />
              </div>
            )}
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && bun run build`
Expected: build succeeds with no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/calls/AudioPlayer.tsx frontend/src/components/calls/CallDrawer.tsx
git commit -m "feat(dashboard): tool call and KB markers on the call audio timeline"
```

---

### Task 5: Full verification sweep

**Files:** none (verification only).

- [ ] **Step 1: Worker tests**

Run: `cd worker && uv run --only-group dev pytest`
Expected: all pass.

- [ ] **Step 2: Backend tests (must be untouched and green)**

Run: `cd backend && uv run pytest`
Expected: all pass — this proves the contract tests are unaffected by the additive fields.

- [ ] **Step 3: Frontend build + lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: clean build, no lint errors.

- [ ] **Step 4: pre-commit sweep**

Run: `pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 5: Done — hand off**

Use the superpowers:finishing-a-development-branch skill to open the PR (title: `feat: tool call blocks in call transcript + audio timeline markers` — must be conventional-commit shaped for the `pr-title` check).
