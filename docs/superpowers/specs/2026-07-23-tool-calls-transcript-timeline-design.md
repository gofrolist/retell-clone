# Tool call blocks in call transcript + audio timeline markers

Date: 2026-07-23
Status: approved

## Goal

Match Retell's call-history detail view in two ways:

1. **Tool Invocation / Tool Result blocks** rendered inline in the
   Transcription tab, chronologically between utterances, with the tool name,
   `tool_call_id`, and pretty-printed JSON payloads (see Retell screenshot in
   the originating conversation).
2. **Markers on the audio player timeline** for tool calls and knowledge-base
   retrievals, with popup annotations on hover and click-to-seek.

## Current state (explored 2026-07-23)

- Worker already records tool events chronologically in `CallState.items`
  (`worker/src/arhiteq_worker/state.py`):
  - `{"role": "agent"|"user", "content": str}`
  - `{"role": "tool_call_invocation", "name": str, "arguments": str}`
  - `{"role": "tool_call_result", "name": str, "content": str}`
- `build_finalize_payload` sends `transcript_object` (utterances only) and
  `transcript_with_tool_calls` (all items). Backend persists both as opaque
  JSON (`Call.transcript_with_tool_calls`, `models.py`) and serves them on the
  call API (`schemas.py`). `FinalizeRequest.transcript_with_tool_calls` is
  `list[Any]` — extra per-item fields flow through with no backend change.
- Frontend drops the data: `RawCall` (`frontend/src/lib/api.ts`) never reads
  `transcript_with_tool_calls`, the adapter hardcodes `time: ""`, and
  `Transcript.tsx` only renders `agent | user | kb_retrieval` turns.
- `AudioPlayer.tsx` has a custom seekable bar (absolutely-positioned fill +
  scrubber) but no marker support. `components/ui/HoverCard.tsx` is a portal
  popover suitable for marker popups (escapes the drawer's overflow clipping).

**Key gap:** no item in the pipeline carries a timestamp, so markers cannot be
positioned for existing calls. Decision: capture real timestamps in the worker;
calls recorded before deploy show tool blocks but no timeline markers.

## Design

### 1. Worker: timing capture

- `CallState` records a monotonic anchor at creation (≈ call answer ≈
  recording start).
- `add_message`, `add_tool_invocation`, `add_tool_result` stamp each item with
  `time_ms: int` — milliseconds since the anchor.
- `add_tool_invocation` generates a sequential `tool_call_id`
  (`tool_call_1`, …) and returns it. `add_tool_result` accepts an optional
  explicit `tool_call_id`; every paired call site in `tools.py` captures the
  id returned by `add_tool_invocation` and passes it to the matching
  `add_tool_result`, since concurrent same-name tool calls (livekit-agents
  allows an `await` between invocation and result at every call site) can
  otherwise make the newest-unmatched-invocation heuristic pair a result with
  the wrong invocation. The heuristic remains as a fallback when no explicit
  id is given.
- These are **additive** fields on items inside `transcript_with_tool_calls`
  and `transcript_object`; the frozen Retell wire contract allows extra
  fields. No rename or removal of existing fields.
- Finalize payload construction unchanged apart from the enriched items.
  Mid-call `transcript_update` events are out of scope (the drawer refetches
  the full call).
- Honesty note: `time_ms` uses the wall-clock `now_ms()` delta from
  `answered_at_ms` (consistent with existing timestamps); recording egress
  starts slightly after the anchor, so marker positions are approximate to
  well under a second — do not tighten without moving the anchor.

### 2. Backend

- No changes. `transcript_with_tool_calls` is `list[Any]` end to end.

### 3. Frontend data plumbing

- `RawCall` gains
  `transcript_with_tool_calls?: { role: string; content?: string; name?: string; arguments?: string; tool_call_id?: string; time_ms?: number }[]`.
- `TranscriptTurn` in `types.ts` is renamed/widened to `TranscriptItem`:
  `{ role: "agent" | "user" | "kb_retrieval" | "tool_invocation" | "tool_result"; content: string; name?: string; tool_call_id?: string; time_ms?: number; time: string }`
  (snake_case, matching the codebase's `DetailLog.time_ms` convention). The
  existing `Call.transcript` field carries it — no parallel field.
- Adapter `uiCallFromRaw` maps `transcript_with_tool_calls` →
  `Call.transcript` when non-empty; otherwise falls back to the existing
  `transcript_object` mapping (old calls keep working, minus tool blocks and
  markers). Real `m:ss` times replace the hardcoded `time: ""` when `time_ms`
  is present.

### 4. Transcript UI (`Transcript.tsx`)

- Consumes `TranscriptItem[]` (existing turn rendering preserved for
  agent/user/kb_retrieval).
- New collapsible blocks styled after Retell:
  - Header row: chevron, "Tool Invocation: {name}" (or "Tool Result"), and the
    `m:ss` timestamp right-aligned.
  - Body: `tool_call_id` line plus pretty-printed JSON (`arguments` for
    invocations, `content` for results) in a monospace card. Non-JSON content
    renders verbatim.
  - Expanded by default, collapsible per block.

### 5. Audio timeline markers (`AudioPlayer.tsx`)

- New optional prop
  `markers?: { time_ms: number; kind: "tool" | "kb"; title: string; body?: string }[]`
  (`AudioMarker`, exported from `AudioPlayer.tsx`).
- Each marker renders as a small dot absolutely positioned on the seek bar at
  `left: time_ms / durationMs`. Hover opens a `HoverCard` popup with the
  annotation (tool name + args/result snippet, or "Knowledge Base
  Retrieval"); click seeks the audio to `time_ms`.
- `CallDrawer.tsx` builds markers from `Call.transcript` (tool invocations —
  one marker per invocation, popup includes its paired result — and KB
  retrievals) and passes them to the player. Items without `time_ms` produce
  no marker.
- Honesty note: KB-retrieval markers are currently exercised only by
  mock/demo data — no production component emits `kb_retrieval` items until a
  kb_lookup/retrieval event source exists.

## Error handling

- Malformed / non-JSON `arguments` or result `content`: render raw string.
- `time_ms` missing or beyond `duration_ms`: no marker for that item (clamp is
  not attempted; absence is safer than a wrong position).
- Calls with no `transcript_with_tool_calls`: identical behavior to today.

## Testing

- Worker: unit tests assert `time_ms` stamping/clamping and `tool_call_id`
  pairing on items and in the finalize payload (`test_state.py`; existing
  `test_tools.py` assertions stay untouched).
- Backend: no changes; existing contract tests must stay green (fields are
  additive).
- Frontend: `bun run build` clean; adapter fallback covered by rendering an
  old-shape call without `transcript_with_tool_calls`.
