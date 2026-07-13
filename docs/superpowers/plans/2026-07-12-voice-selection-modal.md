# Select Voice Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the agent editor's native `<select>` voice picker with a Retell-style "Select Voice" modal (Cartesia enabled, other providers disabled with "Coming soon" tooltips), backed by `recommended` flags and hosted preview audio from the backend.

**Architecture:** Backend additively extends the frozen Retell-shaped `/list-voices` / `/get-voice` responses (`recommended` flag; `preview_audio_url` filled only when a committed mp3 exists under a new public `/static` mount). Frontend builds the modal from existing hand-rolled primitives (`Modal`, `UnderlineTabs`, `Select`, `SearchInput`, `Button`, `Badge`, `EmptyState`, `VoiceAvatar`) plus one new `Tooltip` primitive and a `useVoicePreview` hook; selection flows through the existing `onVoice` → `setAgentField("voice_id", …)` draft/Save plumbing, unchanged.

**Tech Stack:** FastAPI + pytest (backend, Python 3.14, uv), Next.js 16 + React 19 + Tailwind v4 + lucide-react (frontend, bun), Cartesia `/tts/bytes` REST API (one-time preview generation script).

**Spec:** `docs/superpowers/specs/2026-07-12-voice-selection-modal-design.md`

## Global Constraints

- Branch: `feat/voice-selection-modal` (already exists; all commits go there).
- The wire contract is frozen: only ADD fields to voice objects — never rename, drop, or re-nest existing fields (`voice_id`, `voice_name`, `provider`, `accent`, `gender`, `age`, `preview_audio_url`).
- Backend commands run from `backend/`: `uv run pytest` (first time: `uv sync`).
- Frontend commands run from `frontend/` with **bun**: `bun run build`, `bunx eslint src` (there is NO frontend unit-test runner — do not add one; verification is build + lint + manual).
- **Next.js caveat:** `frontend/AGENTS.md` warns this Next.js version differs from training data. All new frontend files here are plain `"use client"` components with no Next-specific APIs, which is safe; if you touch anything Next-specific (routing, params, config), read the relevant guide in `frontend/node_modules/next/dist/docs/` first.
- Styling: Tailwind v4 semantic tokens used in this repo — `line`, `sub`, `faint`, `accent`, `accent-deep`, `app`, `card`, `ink`, `bad`; text size `text-[13px]`; radii `rounded-lg`/`rounded-xl`; `cn()` from `@/lib/utils` for class merging.
- pre-commit hooks (gitleaks, ruff check+format, pytest, eslint) run on every commit; do not bypass them.
- Copy rules: disabled provider tabs and the Add custom voice button say exactly **"Coming soon"**; modal title is **"Select Voice"**; row action button is **"Use Voice"**.

---

### Task 1: Backend — `recommended` flag on the voice catalog

**Files:**
- Modify: `backend/src/architeq_api/voices.py`
- Test: `backend/tests/contract/test_voices.py`

**Interfaces:**
- Produces: every dict in `VOICES` gains a `"recommended": bool` key. Exactly these four are `True`: `cartesia-sonic`, `cartesia-savannah`, `cartesia-blake`, `cartesia-jacqueline` (mix of genders/accents/ages).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/contract/test_voices.py`:

```python
async def test_list_voices_includes_recommended_flags(client):
    resp = await client.get("/list-voices", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    voices = resp.json()
    # Additive field: present on every voice, boolean.
    assert all(isinstance(v.get("recommended"), bool) for v in voices)
    recommended = {v["voice_id"] for v in voices if v["recommended"]}
    assert recommended == {
        "cartesia-sonic",
        "cartesia-savannah",
        "cartesia-blake",
        "cartesia-jacqueline",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/contract/test_voices.py::test_list_voices_includes_recommended_flags -v`
Expected: FAIL — `assert all(...)` is False (no voice has `recommended`).

- [ ] **Step 3: Add the field to the catalog**

In `backend/src/architeq_api/voices.py`, add `"recommended": ...` to every entry (after `"preview_audio_url"`): `True` for `cartesia-sonic`, `cartesia-savannah`, `cartesia-blake`, `cartesia-jacqueline`; `False` for the other eight. Example for the first entry:

```python
    {
        "voice_id": "cartesia-sonic",
        "voice_name": "Sonic",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": True,
    },
```

Also extend the module docstring's last line with: `` `recommended` marks the voices surfaced as cards in the dashboard's voice picker. ``

- [ ] **Step 4: Run the voice tests**

Run: `cd backend && uv run pytest tests/contract/test_voices.py -v`
Expected: all PASS (existing tests only assert a field *subset*, so additive is safe).

- [ ] **Step 5: Commit**

```bash
git add backend/src/architeq_api/voices.py backend/tests/contract/test_voices.py
git commit -m "feat(api): add recommended flag to voice catalog"
```

---

### Task 2: Backend — `/static` mount and preview URL building

**Files:**
- Modify: `backend/src/architeq_api/config.py` (add `public_api_url` setting)
- Modify: `backend/src/architeq_api/main.py` (mount StaticFiles)
- Modify: `backend/src/architeq_api/api/voices.py` (fill `preview_audio_url`)
- Create: `backend/src/architeq_api/static/voice_previews/.gitkeep` (empty file)
- Test: `backend/tests/contract/test_voices.py`

**Interfaces:**
- Consumes: `VOICES` / `VOICES_BY_ID` from Task 1 (shape unchanged apart from `recommended`).
- Produces:
  - `Settings.public_api_url: str = ""` (env `ARCHITEQ_PUBLIC_API_URL`).
  - `architeq_api.api.voices.PREVIEWS_DIR: Path` — module-level, monkeypatchable in tests.
  - `GET /list-voices` / `GET /get-voice/{id}` return `preview_audio_url` as `"{public_api_url}/static/voice_previews/{voice_id}.mp3"` when `PREVIEWS_DIR/{voice_id}.mp3` exists on disk (relative `/static/...` path when the setting is empty), else `None`.
  - `GET /static/voice_previews/*.mp3` serves committed files without auth.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/contract/test_voices.py`:

```python
async def test_preview_audio_url_null_when_sample_missing(client, tmp_path, monkeypatch):
    from architeq_api.api import voices as voices_api

    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)  # empty dir
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert resp.json()["preview_audio_url"] is None


async def test_preview_audio_url_relative_when_sample_exists(client, tmp_path, monkeypatch):
    from architeq_api.api import voices as voices_api

    (tmp_path / "cartesia-sonic.mp3").write_bytes(b"ID3 fake mp3")
    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert resp.json()["preview_audio_url"] == "/static/voice_previews/cartesia-sonic.mp3"
    # And the same voice in the list response carries the same URL.
    listed = (await client.get("/list-voices", headers=AUTH_HEADERS)).json()
    sonic = next(v for v in listed if v["voice_id"] == "cartesia-sonic")
    assert sonic["preview_audio_url"] == "/static/voice_previews/cartesia-sonic.mp3"


async def test_preview_audio_url_absolute_with_public_api_url(client, tmp_path, monkeypatch):
    from architeq_api.api import voices as voices_api
    from architeq_api.config import get_settings

    (tmp_path / "cartesia-sonic.mp3").write_bytes(b"ID3 fake mp3")
    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(get_settings(), "public_api_url", "https://api.example.com/")
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert (
        resp.json()["preview_audio_url"]
        == "https://api.example.com/static/voice_previews/cartesia-sonic.mp3"
    )


async def test_static_mount_serves_preview_files_without_auth(client):
    from architeq_api.api.voices import PREVIEWS_DIR

    sample = PREVIEWS_DIR / "test-sample.mp3"
    sample.write_bytes(b"ID3 test bytes")
    try:
        resp = await client.get("/static/voice_previews/test-sample.mp3")
        assert resp.status_code == 200
        assert resp.content == b"ID3 test bytes"
    finally:
        sample.unlink()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/contract/test_voices.py -v`
Expected: the four new tests FAIL (`PREVIEWS_DIR` doesn't exist → AttributeError; static route 404). Pre-existing tests still pass.

- [ ] **Step 3: Implement**

`backend/src/architeq_api/config.py` — add after the `recordings_gcs_bucket` field:

```python
    # Public base URL of this API (e.g. https://api.usanretirement.com), used
    # to build absolute preview_audio_url links; empty = relative /static/...
    public_api_url: str = ""
```

`backend/src/architeq_api/api/voices.py` — replace the whole file:

```python
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_api_key
from ..config import get_settings
from ..models import ApiKey
from ..voices import VOICES, VOICES_BY_ID

router = APIRouter(tags=["voices"])

# Committed preview mp3s (generated by backend/scripts/generate_voice_previews.py).
PREVIEWS_DIR = Path(__file__).resolve().parent.parent / "static" / "voice_previews"


def _with_preview(voice: dict[str, Any]) -> dict[str, Any]:
    """Fill preview_audio_url when the voice's sample file exists on disk."""
    if not (PREVIEWS_DIR / f"{voice['voice_id']}.mp3").is_file():
        return voice
    base = get_settings().public_api_url.rstrip("/")
    return {
        **voice,
        "preview_audio_url": f"{base}/static/voice_previews/{voice['voice_id']}.mp3",
    }


@router.get("/list-voices")
async def list_voices(api_key: ApiKey = Depends(require_api_key)):
    return [_with_preview(v) for v in VOICES]


@router.get("/get-voice/{voice_id}")
async def get_voice(voice_id: str, api_key: ApiKey = Depends(require_api_key)):
    voice = VOICES_BY_ID.get(voice_id)
    if voice is None:
        raise HTTPException(404, detail="Voice not found")
    return _with_preview(voice)
```

`backend/src/architeq_api/main.py` — add imports and the mount. New imports at the top:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

After the last `app.include_router(...)` line:

```python
# Public, read-only assets (voice preview mp3s). No auth: previews must be
# playable from a bare <audio> tag in the dashboard.
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
```

Create the directory with an empty keep-file so StaticFiles (and git) see it:

```bash
mkdir -p backend/src/architeq_api/static/voice_previews
touch backend/src/architeq_api/static/voice_previews/.gitkeep
```

- [ ] **Step 4: Run the backend test suite**

Run: `cd backend && uv run pytest tests/contract/test_voices.py -v && uv run pytest -q`
Expected: all PASS (full suite too — the new mount must not break other contract tests).

- [ ] **Step 5: Confirm the Docker image will contain the static dir**

Run: `grep -n "COPY" backend/Dockerfile`
Expected: a `COPY` that includes `src/` (or the whole backend dir). If `src/` is copied, the mp3s ship automatically. If the Dockerfile copies narrower paths, add `COPY src/architeq_api/static ...` accordingly and note it in the commit.

- [ ] **Step 6: Commit**

```bash
git add backend/src/architeq_api/config.py backend/src/architeq_api/main.py \
  backend/src/architeq_api/api/voices.py backend/src/architeq_api/static/voice_previews/.gitkeep \
  backend/tests/contract/test_voices.py
git commit -m "feat(api): serve voice preview audio from /static with public_api_url links"
```

---

### Task 3: Backend — preview generation script

**Files:**
- Create: `backend/scripts/generate_voice_previews.py`

**Interfaces:**
- Consumes: `VOICES` from `architeq_api.voices`; writes into Task 2's `backend/src/architeq_api/static/voice_previews/`.
- Produces: `{voice_id}.mp3` per catalog voice. Manual, one-time: `cd backend && CARTESIA_API_KEY=... uv run python scripts/generate_voice_previews.py`. Idempotent (skips existing files).

There is no automated test for this script (it needs a live Cartesia key); ruff lints it at commit time. **Do not run it during plan execution unless `CARTESIA_API_KEY` is available in the environment** — if it is, run it and commit the mp3s in this task's commit; otherwise commit the script alone and leave mp3 generation as a documented operator step (the UI degrades gracefully: play buttons stay disabled).

- [ ] **Step 1: Write the script**

Create `backend/scripts/generate_voice_previews.py`:

```python
"""Generate preview mp3s for the voice catalog via Cartesia /tts/bytes.

One-time, manual, idempotent:

    cd backend && CARTESIA_API_KEY=... uv run python scripts/generate_voice_previews.py

Writes src/architeq_api/static/voice_previews/{voice_id}.mp3 (committed to
git; ~50 KB each). The API fills preview_audio_url only for files that exist,
so a partial run is safe. Re-run with a voice removed from the skip check to
regenerate it.
"""

import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from architeq_api.voices import VOICES  # noqa: E402

# Mirrors worker/src/architeq_worker/voices.py VOICE_UUIDS — keep in sync so
# previews are synthesized with the exact voice used on live calls.
VOICE_UUIDS: dict[str, str] = {
    "cartesia-sonic": "694f9389-aac1-45b6-b726-9d9369183238",
    "cartesia-sonic-english": "694f9389-aac1-45b6-b726-9d9369183238",
    "cartesia-savannah": "78ab82d5-25be-4f7d-82b3-7ad64e5b85b2",
    "cartesia-brooke": "e07c00bc-4134-4eae-9ea4-1a55fb45746b",
    "cartesia-katie": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "cartesia-jacqueline": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
    "cartesia-blake": "a167e0f3-df7e-4d52-a9c3-f949145efdab",
    "cartesia-ronald": "5ee9feff-1265-424a-9d7f-8e4d431a12c7",
    "cartesia-connor": "92c41dd4-04aa-45de-8504-a92b40cb8818",
    "cartesia-griffin": "c99d36f3-5ffd-4253-803a-535c1bc9c306",
    "cartesia-daniela": "5c5ad5e7-1020-476b-8b91-fdcbe9cc313c",
    "cartesia-luca": "e019ed7e-6079-4467-bc7f-b599a5dccf6f",
}

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "architeq_api" / "static" / "voice_previews"
TTS_MODEL = os.getenv("ARCHITEQ_CARTESIA_TTS_MODEL", "sonic-2")  # match the worker


def main() -> int:
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        print("CARTESIA_API_KEY is required", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    with httpx.Client(
        base_url="https://api.cartesia.ai",
        headers={"X-API-Key": api_key, "Cartesia-Version": "2025-04-16"},
        timeout=60.0,
    ) as client:
        for voice in VOICES:
            voice_id = voice["voice_id"]
            out = OUT_DIR / f"{voice_id}.mp3"
            if out.exists():
                print(f"skip {voice_id} (exists)")
                continue
            uuid = VOICE_UUIDS.get(voice_id)
            if uuid is None:
                print(f"skip {voice_id} (no Cartesia UUID mapping)", file=sys.stderr)
                failures += 1
                continue
            resp = client.post(
                "/tts/bytes",
                json={
                    "model_id": TTS_MODEL,
                    "transcript": (
                        f"Hi, I'm {voice['voice_name']}. "
                        "This is a preview of how I sound on your calls."
                    ),
                    "voice": {"mode": "id", "id": uuid},
                    "language": "en",
                    "output_format": {
                        "container": "mp3",
                        "sample_rate": 44100,
                        "bit_rate": 128000,
                    },
                },
            )
            if resp.status_code != 200:
                print(f"FAIL {voice_id}: HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr)
                failures += 1
                continue
            out.write_bytes(resp.content)
            print(f"wrote {out.relative_to(OUT_DIR.parent.parent.parent)} ({len(resp.content)} bytes)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Lint and (only if a key is available) run it**

Run: `cd backend && uv run ruff check scripts/generate_voice_previews.py && uv run ruff format --check scripts/generate_voice_previews.py`
Expected: clean (fix any formatting with `uv run ruff format scripts/`).

If `CARTESIA_API_KEY` is set in the environment: `cd backend && uv run python scripts/generate_voice_previews.py` — expected `wrote .../cartesia-*.mp3` × 12, then re-run prints `skip … (exists)` × 12 (idempotency check). Then run `uv run pytest tests/contract/test_voices.py -v` — all PASS (list responses now carry relative preview URLs).

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/generate_voice_previews.py
# plus, only if generated: git add backend/src/architeq_api/static/voice_previews/*.mp3
git commit -m "feat(api): script to generate Cartesia voice preview mp3s"
```

---

### Task 4: Frontend — `Voice` type and `Tooltip` primitive

**Files:**
- Modify: `frontend/src/lib/types.ts` (add `Voice` interface)
- Modify: `frontend/src/lib/api.ts` (type `listVoices` with it)
- Create: `frontend/src/components/ui/Tooltip.tsx`

**Interfaces:**
- Produces:
  - `Voice` (exported from `@/lib/types`): `{ voice_id: string; voice_name: string; provider: string; gender?: string; accent?: string; age?: string; preview_audio_url?: string | null; recommended?: boolean }`.
  - `api.listVoices(): Promise<Voice[]>`.
  - `Tooltip` (default export from `@/components/ui/Tooltip`): props `{ label: string; children: ReactNode; className?: string }` — pure-CSS hover tooltip that works around disabled buttons (hover is detected on the wrapper, not the button).

- [ ] **Step 1: Add the `Voice` interface**

In `frontend/src/lib/types.ts`, below the `Agent` interface, add:

```ts
/** /list-voices catalog entry (Retell voice shape + Architeq extras). */
export interface Voice {
  voice_id: string;
  voice_name: string;
  provider: string;
  gender?: string;
  accent?: string;
  age?: string;
  preview_audio_url?: string | null;
  recommended?: boolean;
}
```

- [ ] **Step 2: Use it in `api.ts`**

In `frontend/src/lib/api.ts`: add `Voice` to the existing `import type { ... } from "./types"` list, and replace the `listVoices` entry (currently an inline object type) with:

```ts
  // ------------------------------------------------------------- voices
  listVoices: () => request<Voice[]>("/list-voices"),
```

- [ ] **Step 3: Create the Tooltip primitive**

Create `frontend/src/components/ui/Tooltip.tsx`:

```tsx
"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/**
 * Pure-CSS hover tooltip. Hover is detected on the wrapper span, so it also
 * works around disabled buttons (which swallow JS mouse events but still
 * let the parent match :hover).
 */
export default function Tooltip({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("group/tooltip relative inline-flex", className)}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-ink px-2 py-1 text-[11px] font-medium text-white opacity-0 shadow-sm transition-opacity group-hover/tooltip:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}
```

- [ ] **Step 4: Build**

Run: `cd frontend && bun run build`
Expected: compiles with no type errors (Tooltip is not imported anywhere yet — that's fine).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/ui/Tooltip.tsx
git commit -m "feat(dashboard): Voice type and Tooltip primitive"
```

---

### Task 5: Frontend — `useVoicePreview` hook

**Files:**
- Create: `frontend/src/components/voices/useVoicePreview.ts`

**Interfaces:**
- Consumes: `API_BASE` (already exported from `@/lib/api`).
- Produces (named exports):
  - `resolvePreviewUrl(url: string | null | undefined): string | null` — absolute `http(s)` URLs pass through; relative `/static/...` paths are resolved against `API_BASE`; anything else → `null` (same security posture as `AudioPlayer`'s safeSrc guard).
  - `useVoicePreview(): { playingId: string | null; toggle: (voiceId: string, previewUrl: string | null | undefined) => void }` — one shared Audio element; starting a preview stops the previous one; unmount stops playback.

- [ ] **Step 1: Write the hook**

Create `frontend/src/components/voices/useVoicePreview.ts`:

```ts
"use client";

import { API_BASE } from "@/lib/api";
import { useEffect, useRef, useState } from "react";

/**
 * Same guard as AudioPlayer's safeSrc: only http(s) plays. The API returns a
 * relative /static/... path when ARCHITEQ_PUBLIC_API_URL is unset (local
 * dev); resolve it against the API origin, not the dashboard origin.
 */
export function resolvePreviewUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^https?:/i.test(url)) return url;
  if (url.startsWith("/static/")) return `${API_BASE}${url}`;
  return null;
}

/** One shared Audio element: starting a preview stops the previous one. */
export function useVoicePreview() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);

  useEffect(
    () => () => {
      audioRef.current?.pause();
      audioRef.current = null;
    },
    [],
  );

  const toggle = (voiceId: string, previewUrl: string | null | undefined) => {
    if (playingId === voiceId) {
      audioRef.current?.pause();
      audioRef.current = null;
      setPlayingId(null);
      return;
    }
    const src = resolvePreviewUrl(previewUrl);
    if (!src) return;
    audioRef.current?.pause();
    const audio = new Audio(src);
    audioRef.current = audio;
    audio.onended = () => setPlayingId(null);
    audio.onerror = () => setPlayingId(null);
    setPlayingId(voiceId);
    audio.play().catch(() => setPlayingId(null));
  };

  return { playingId, toggle };
}
```

- [ ] **Step 2: Build**

Run: `cd frontend && bun run build`
Expected: compiles clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/voices/useVoicePreview.ts
git commit -m "feat(dashboard): shared voice preview playback hook"
```

---

### Task 6: Frontend — `SelectVoiceModal`

**Files:**
- Create: `frontend/src/components/voices/SelectVoiceModal.tsx`

**Interfaces:**
- Consumes: `Modal`, `UnderlineTabs`, `Select`, `SearchInput`, `Button`, `Badge`, `EmptyState`, `Tooltip` primitives; `VoiceAvatar` from `@/components/agents/AgentsTable`; `Voice` from `@/lib/types`; `voiceNameFromId` from `@/lib/api`; `useVoicePreview` from Task 5.
- Produces: `SelectVoiceModal` (default export), props:
  `{ voices: Voice[]; currentVoiceId: string; onSelect: (voiceId: string) => void; onClose: () => void }`.
  The caller renders it conditionally (`{open && <SelectVoiceModal …/>}`) so selection state resets on every open. `onSelect` fires on **Use Voice** (immediately, then closes) or **Save** (then closes); **Cancel**/backdrop/Escape close without selecting.

- [ ] **Step 1: Write the component**

Create `frontend/src/components/voices/SelectVoiceModal.tsx`:

```tsx
"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import Modal from "@/components/ui/Modal";
import SearchInput from "@/components/ui/SearchInput";
import Select from "@/components/ui/Select";
import { UnderlineTabs } from "@/components/ui/Tabs";
import Tooltip from "@/components/ui/Tooltip";
import { voiceNameFromId } from "@/lib/api";
import type { Voice } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AudioLines, Check, Pause, Play, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useVoicePreview } from "./useVoicePreview";

const TOP_TABS = [
  { key: "platform", label: "Platform Voices" },
  { key: "custom", label: "Custom Providers" },
];

// Only Cartesia ships today; the rest are visible but disabled (Retell parity).
const PROVIDERS = [
  { key: "minimax", label: "MiniMax", enabled: false },
  { key: "fish", label: "Fish Audio", enabled: false },
  { key: "elevenlabs", label: "ElevenLabs", enabled: false },
  { key: "cartesia", label: "Cartesia", enabled: true },
  { key: "openai", label: "OpenAI", enabled: false },
];

const GENDERS = [
  { value: "all", label: "Gender" },
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
];

const AGES = [
  { value: "all", label: "Age" },
  { value: "Young", label: "Young" },
  { value: "Middle Aged", label: "Middle Aged" },
  { value: "Old", label: "Old" },
];

function traitLine(v: Voice): string {
  return [v.accent, v.age].filter(Boolean).join(" · ");
}

function PlayButton({
  voice,
  playingId,
  onToggle,
}: {
  voice: Voice;
  playingId: string | null;
  onToggle: (voiceId: string, previewUrl: string | null | undefined) => void;
}) {
  const canPlay = Boolean(voice.preview_audio_url);
  const playing = playingId === voice.voice_id;
  return (
    <button
      disabled={!canPlay}
      title={canPlay ? undefined : "Preview not available yet"}
      onClick={(e) => {
        e.stopPropagation();
        onToggle(voice.voice_id, voice.preview_audio_url);
      }}
      aria-label={`${playing ? "Pause" : "Play"} ${voice.voice_name} preview`}
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-full border border-line bg-white transition-colors",
        canPlay ? "cursor-pointer hover:bg-app" : "opacity-40 cursor-not-allowed",
      )}
    >
      {playing ? (
        <Pause className="size-3.5" />
      ) : (
        <Play className="size-3.5 translate-x-px" />
      )}
    </button>
  );
}

export default function SelectVoiceModal({
  voices,
  currentVoiceId,
  onSelect,
  onClose,
}: {
  voices: Voice[];
  currentVoiceId: string;
  onSelect: (voiceId: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState("platform");
  const [gender, setGender] = useState("all");
  const [accent, setAccent] = useState("all");
  const [age, setAge] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(currentVoiceId);
  const { playingId, toggle } = useVoicePreview();

  const accents = useMemo(() => {
    const distinct = [...new Set(voices.map((v) => v.accent).filter(Boolean))] as string[];
    return [{ value: "all", label: "Accent" }, ...distinct.sort().map((a) => ({ value: a, label: a }))];
  }, [voices]);

  const filtersActive = gender !== "all" || accent !== "all" || age !== "all" || search !== "";

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return voices.filter(
      (v) =>
        (gender === "all" || v.gender === gender) &&
        (accent === "all" || v.accent === accent) &&
        (age === "all" || v.age === age) &&
        (q === "" ||
          v.voice_name.toLowerCase().includes(q) ||
          v.voice_id.toLowerCase().includes(q)),
    );
  }, [voices, gender, accent, age, search]);

  const recommended = voices.filter((v) => v.recommended);
  const selectedVoice = voices.find((v) => v.voice_id === selected);

  const applyVoice = (voiceId: string) => {
    onSelect(voiceId);
    onClose();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Select Voice"
      width="max-w-5xl"
      footer={
        <div className="flex w-full items-center justify-between">
          <div className="flex items-center gap-2.5">
            {selected ? (
              <>
                <VoiceAvatar
                  name={selectedVoice?.voice_name ?? voiceNameFromId(selected)}
                  index={0}
                />
                <div>
                  <div className="text-[13px] font-medium leading-tight">
                    {selectedVoice?.voice_name ?? voiceNameFromId(selected)}
                  </div>
                  {selectedVoice && (
                    <div className="text-xs text-sub leading-tight">
                      {traitLine(selectedVoice)}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <span className="text-[13px] text-sub">No voice selected</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!selected} onClick={() => applyVoice(selected)}>
              Save
            </Button>
          </div>
        </div>
      }
    >
      <UnderlineTabs tabs={TOP_TABS} active={tab} onChange={setTab} />

      {tab === "custom" && (
        <div className="mt-4 grid grid-cols-5 gap-0.5 rounded-lg border border-line bg-app p-0.5">
          {PROVIDERS.map((p) =>
            p.enabled ? (
              <button
                key={p.key}
                className="rounded-md border border-line bg-white px-3 py-1.5 text-center text-[13px] font-medium text-ink shadow-sm"
              >
                {p.label}
              </button>
            ) : (
              <Tooltip key={p.key} label="Coming soon" className="w-full">
                <button
                  disabled
                  className="w-full rounded-md px-3 py-1.5 text-center text-[13px] font-medium text-faint cursor-not-allowed"
                >
                  {p.label}
                </button>
              </Tooltip>
            ),
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Tooltip label="Coming soon">
          <button
            disabled
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-ink px-3 text-[13px] font-medium text-white opacity-40 cursor-not-allowed"
          >
            <Plus className="size-4" />
            Add custom voice
          </button>
        </Tooltip>
        <Select value={gender} onChange={setGender} options={GENDERS} className="w-32" />
        <Select value={accent} onChange={setAccent} options={accents} className="w-32" />
        <Select value={age} onChange={setAge} options={AGES} className="w-36" />
        <SearchInput value={search} onChange={setSearch} className="min-w-48 grow" />
      </div>

      {!filtersActive && recommended.length > 0 && (
        <div className="mt-4">
          <h3 className="text-[13px] font-semibold">Recommended Voices</h3>
          <div className="mt-2 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {recommended.map((v, i) => (
              <button
                key={v.voice_id}
                onClick={() => setSelected(v.voice_id)}
                className={cn(
                  "flex items-center gap-2.5 rounded-xl border p-3 text-left transition-colors cursor-pointer",
                  selected === v.voice_id
                    ? "border-accent ring-2 ring-accent/15"
                    : "border-line hover:bg-app",
                )}
              >
                <VoiceAvatar name={v.voice_name} index={i} />
                <span className="min-w-0 grow">
                  <span className="block truncate text-[13px] font-medium leading-tight">
                    {v.voice_name}
                  </span>
                  <span className="block truncate text-xs text-sub leading-tight">
                    {traitLine(v)}
                  </span>
                  <span className="block truncate text-xs text-faint leading-tight">
                    ID: {v.voice_id}
                  </span>
                </span>
                <PlayButton voice={v} playingId={playingId} onToggle={toggle} />
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4">
        {filtered.length === 0 ? (
          <EmptyState
            icon={AudioLines}
            title="No voices match"
            description="Try clearing the filters or searching for a different name."
          />
        ) : (
          <table className="w-full border-separate border-spacing-0 text-[13px]">
            <thead>
              <tr className="text-left text-xs text-sub">
                <th className="rounded-l-lg border-y border-l border-line bg-app px-3 py-2 font-medium">
                  Voice
                </th>
                <th className="border-y border-line bg-app px-3 py-2 font-medium">Trait</th>
                <th className="rounded-r-lg border-y border-r border-line bg-app px-3 py-2 font-medium">
                  Voice ID
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((v, i) => (
                <tr
                  key={v.voice_id}
                  onClick={() => setSelected(v.voice_id)}
                  className={cn(
                    "group/row cursor-pointer",
                    selected === v.voice_id ? "bg-accent/5" : "hover:bg-app",
                  )}
                >
                  <td className="border-b border-line px-3 py-2.5">
                    <span className="flex items-center gap-2.5">
                      <PlayButton voice={v} playingId={playingId} onToggle={toggle} />
                      <VoiceAvatar name={v.voice_name} index={i} />
                      <span className="font-medium">{v.voice_name}</span>
                    </span>
                  </td>
                  <td className="border-b border-line px-3 py-2.5">
                    <span className="flex items-center gap-1.5">
                      {v.accent && <Badge tone="gray">{v.accent}</Badge>}
                      {v.age && <Badge tone="gray">{v.age}</Badge>}
                    </span>
                  </td>
                  <td className="border-b border-line px-3 py-2.5">
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-sub">{v.voice_id}</span>
                      <Button
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          applyVoice(v.voice_id);
                        }}
                        className={cn(
                          "invisible group-hover/row:visible",
                          selected === v.voice_id && "visible",
                        )}
                      >
                        <Check className="size-3.5" />
                        Use Voice
                      </Button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Modal>
  );
}
```

- [ ] **Step 2: Build and lint**

Run: `cd frontend && bun run build && bunx eslint src/components/voices`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/voices/SelectVoiceModal.tsx
git commit -m "feat(dashboard): Retell-style Select Voice modal"
```

---

### Task 7: Frontend — wire the modal into the agent editor

**Files:**
- Modify: `frontend/src/components/editor/SelectorRow.tsx`
- Modify: `frontend/src/app/agents/[id]/page.tsx` (voices state type only)

**Interfaces:**
- Consumes: `SelectVoiceModal` (Task 6), `Voice` (Task 4).
- Produces: `SelectorRow`'s `voices` prop becomes `Voice[]`; its voice control is now a button that opens the modal; the `onVoice: (v: string) => void` prop contract is unchanged, so `page.tsx` save plumbing needs no changes beyond the state type.

- [ ] **Step 1: Replace the voice `<select>` with a modal trigger**

In `frontend/src/components/editor/SelectorRow.tsx`:

1. Update imports — add `SelectVoiceModal`, `useState`, `ChevronDown`, `Voice`:

```tsx
import { VoiceAvatar } from "@/components/agents/AgentsTable";
import Select from "@/components/ui/Select";
import SelectVoiceModal from "@/components/voices/SelectVoiceModal";
import { voiceNameFromId } from "@/lib/api";
import { LLM_MODELS } from "@/lib/models";
import type { Voice } from "@/lib/types";
import { withValue } from "@/lib/utils";
import { BookOpen, ChevronDown, Clock4, Settings2, Sparkles } from "lucide-react";
import { useState } from "react";
```

2. Change the `voices` prop type from `{ voice_id: string; voice_name: string }[]` to `Voice[]`.

3. Add modal state at the top of the component body:

```tsx
  const [voiceModalOpen, setVoiceModalOpen] = useState(false);
```

4. Delete the now-unused `voiceOptions` block (the `withValue(voices.map(...), voiceId, ...)` lines 40-44); keep the `voiceName` line.

5. Replace the voice `<Select …/>` (the one with `prefix={<VoiceAvatar …/>}` and `className="[&_select]:pl-10"`) with:

```tsx
      <button
        onClick={() => setVoiceModalOpen(true)}
        className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white pl-2 pr-2.5 text-[13px] font-medium transition-colors hover:bg-app cursor-pointer"
        aria-haspopup="dialog"
      >
        <VoiceAvatar name={voiceName} index={0} />
        {voiceName}
        <ChevronDown className="size-3.5 text-faint" />
      </button>
      {voiceModalOpen && (
        <SelectVoiceModal
          voices={voices}
          currentVoiceId={voiceId}
          onSelect={onVoice}
          onClose={() => setVoiceModalOpen(false)}
        />
      )}
```

- [ ] **Step 2: Widen the voices state type on the agent page**

In `frontend/src/app/agents/[id]/page.tsx`:

1. Add `Voice` to the `import type { ... } from "@/lib/types"` line (or add such an import if the types come from elsewhere — keep existing style).
2. Change line 49 from
   `const [voices, setVoices] = useState<{ voice_id: string; voice_name: string }[]>([]);`
   to
   `const [voices, setVoices] = useState<Voice[]>([]);`

No other page changes: `api.listVoices()` already returns `Voice[]` after Task 4, and `SelectorRow` receives it as-is.

- [ ] **Step 3: Build and lint**

Run: `cd frontend && bun run build && bunx eslint src/components/editor src/app/agents`
Expected: clean. If `withValue` is now unused in `SelectorRow.tsx`, remove it from the import (it is still used for model/language options — check before removing).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/editor/SelectorRow.tsx "frontend/src/app/agents/[id]/page.tsx"
git commit -m "feat(dashboard): open Select Voice modal from agent editor"
```

---

### Task 8: End-to-end verification in the local stack

**Files:** none (verification only; fix-up commits allowed if issues surface).

- [ ] **Step 1: Run the full test/build sweep**

```bash
cd backend && uv run pytest -q
cd ../frontend && bun run build
cd .. && pre-commit run --all-files
```
Expected: all green.

- [ ] **Step 2: Drive the flow in the running app**

Start the stack: `docker compose up -d`, then `make api` and `make web` (separate terminals). Open `http://localhost:3000` (dashboard), navigate to Agents → any agent. Verify, in order:

1. The selector row shows the voice trigger button (avatar + name + chevron); clicking opens the **Select Voice** modal.
2. **Platform Voices** tab shows filters, Recommended Voices cards (4), and the 12-voice table. **Custom Providers** tab additionally shows the provider row; hovering MiniMax / Fish Audio / ElevenLabs / OpenAI shows the "Coming soon" tooltip and they are not clickable; Cartesia renders active.
3. Hovering **＋ Add custom voice** shows "Coming soon"; it is not clickable.
4. Filters: Gender=Male shrinks the table to the 5 male voices and hides the Recommended row; search "sav" leaves only Savannah; clearing everything restores cards + full table.
5. Clicking a row highlights it and the footer shows that voice; **Cancel** closes without changing the editor; reopening + **Use Voice** on a row closes the modal and the editor button now shows that voice with the header Save enabled (dirty).
6. Editor **Save** issues `PATCH /update-agent/...` whose body contains only changed fields including `voice_id` (check the browser network tab).
7. Play buttons: disabled with "Preview not available yet" when mp3s were not generated (no `CARTESIA_API_KEY` during Task 3); if they were generated, clicking plays audio, clicking again pauses, starting a second voice stops the first.
8. Escape and backdrop click close the modal.

- [ ] **Step 3: Report**

Summarize verification results (including whether previews were generated or left as the documented operator step) before opening a PR. PR title must be a conventional commit, e.g. `feat: Retell-style voice selection modal`.
