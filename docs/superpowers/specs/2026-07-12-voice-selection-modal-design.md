# Select Voice modal — design

Date: 2026-07-12
Status: approved

## Goal

Replace the plain native `<select>` voice picker in the agent editor with a
Retell-style "Select Voice" modal (reference: Retell dashboard screenshot,
2026-07-12). Cartesia is the only working provider; all other provider tabs
are visible but disabled with a "Coming soon" tooltip.

## Current state

- Picker: `frontend/src/components/editor/SelectorRow.tsx` renders the shared
  `Select` (native `<select>`) with a `VoiceAvatar` prefix. No search, no
  filters, no preview.
- Data: `GET /list-voices` (backend `arhiteq_api/api/voices.py`) returns the
  static `VOICES` list from `backend/src/arhiteq_api/voices.py` — 12 Cartesia
  voices with `voice_id`, `voice_name`, `provider`, `accent`, `gender`, `age`,
  `preview_audio_url` (currently always `null`).
- Save flow: `agents/[id]/page.tsx` wires `onVoice` →
  `setAgentField("voice_id", v)` into a draft, PATCHed on Save. Unchanged by
  this design.
- UI kit: hand-rolled primitives in `frontend/src/components/ui/` (custom
  `Modal`, `PillTabs`, `SearchInput`, `Select`, `Badge`, …). Tailwind v4 with
  semantic tokens (`line`, `sub`, `faint`, `accent`, `card`, `ink`). No
  Tooltip primitive exists yet.

## Backend changes (contract-safe, additive only)

The wire contract is frozen; we only add fields, never rename or drop.

1. **`recommended` flag** — add `recommended: True` to 4 curated voices in
   `VOICES`. All other voices omit the field or carry `False`.
2. **Preview audio** — populate `preview_audio_url` for all 12 voices:
   - One-time script `scripts/generate_voice_previews.py` (run manually with
     `CARTESIA_API_KEY`) synthesizes a short fixed sample sentence per voice.
   - Output committed as small mp3s at
     `backend/src/arhiteq_api/static/voice_previews/{voice_id}.mp3`.
   - FastAPI mounts `StaticFiles` at `/static` — unauthenticated, read-only
     mp3s only.
   - `preview_audio_url` is built from a `PUBLIC_API_URL` setting; falls back
     to a relative `/static/...` path when unset (local dev).
   - Rationale for API-hosted (vs GCS bucket or frontend assets): the URL
     lives in the API response, so previews work for any Retell-SDK consumer,
     not just our dashboard; no new infra for ~12 × ~50 KB files.

## Frontend changes

New components live under `frontend/src/components/voices/`.

### `SelectVoiceModal.tsx`

Wide modal (`max-w-5xl`) built on the existing `Modal`. Layout top-to-bottom,
matching the Retell reference:

1. **Top-level underline tabs**: `Platform Voices` / `Custom Providers`.
   - Platform Voices: the Cartesia catalog (filters + recommended + table),
     no provider row.
   - Custom Providers: same catalog plus the provider sub-tab row.
2. **Provider row** (Custom Providers tab only): segmented control with
   `MiniMax · Fish Audio · ElevenLabs · Cartesia · OpenAI`. Only Cartesia is
   enabled/active; the rest are muted, non-clickable, wrapped in a `Tooltip`
   saying "Coming soon".
3. **Filter row**:
   - `＋ Add custom voice` button — disabled, "Coming soon" tooltip.
   - `Select`s for **Gender** (All/Female/Male) and **Accent** (All + distinct
     accents from data).
   - **Age** select (All/Young/Middle Aged/Old) — stands in for Retell's
     "Types" filter; it is what our data has.
   - `SearchInput` matching voice name and voice ID (case-insensitive
     substring).
   - Filters combine with AND.
4. **Recommended Voices**: horizontal card row driven by the `recommended`
   flag — avatar, name, `accent · age`, `ID: {voice_id}`, play button. The
   section hides whenever any filter or search is active.
5. **Voice table**: columns Voice (play button, `VoiceAvatar`, name), Trait
   (chips for accent and age), Voice ID (monospace). Clicking a row selects
   it. The hovered/selected row shows a **Use Voice** button that applies the
   voice (calls `onVoice`) and closes the modal in one click (Retell
   behavior). Persistence still goes through the page-level draft/Save flow —
   the modal never PATCHes.
6. **Footer**: left — selected voice (avatar, name, `accent · age`); right —
   `Cancel` / `Save`. Save calls `onVoice(voice_id)` and closes; Cancel
   discards local selection.

### `Tooltip.tsx` (new ui primitive)

Hand-rolled, CSS-positioned tooltip in `frontend/src/components/ui/`,
consistent with existing primitive style. Used for disabled provider tabs and
the Add custom voice button.

### `useVoicePreview` hook

- One shared `Audio` element for the whole modal.
- Toggle play/pause per voice; starting one preview stops the previous;
  closing the modal stops playback.
- Reuses `AudioPlayer`'s security guard: only `https?:` (or relative
  `/static/...`) srcs are played.
- Play buttons render disabled when `preview_audio_url` is missing.

### Integration

- `SelectorRow.tsx`: the voice `<select>` becomes a button (avatar + current
  voice name + chevron, same visual weight as today) that opens
  `SelectVoiceModal`. `onVoice` prop is unchanged.
- `lib/api.ts`: extend the `listVoices` element type with `age`,
  `preview_audio_url`, `recommended`.

## Error handling

- Voice list fetch failure: keep today's silent fallback — trigger button
  shows `voiceNameFromId(voice_id)`; modal shows an `EmptyState` if opened
  with no voices.
- Audio errors (404, decode failure): reset the play button state, no toast.
- Empty filter result: `EmptyState` in the table area.

## Testing

- Backend: pytest for `recommended` / `preview_audio_url` presence in
  `/list-voices`, and the `/static` mount serving an mp3. Existing contract
  tests must stay green (additive change only).
- Frontend: `bun run build` + eslint (pre-commit); manual verification of the
  full flow in the local stack (open modal, filter, preview, Use Voice, Save,
  PATCH payload contains only `voice_id`).

## Out of scope

- Expressive mode, More Settings (voice_model / temperature / speed) in the
  modal footer.
- Custom voice creation; non-Cartesia providers.
- Fallback Voice selector (Security & Fallback accordion) — separate feature.
- Photo avatars — we keep the existing initial-based `VoiceAvatar`.
