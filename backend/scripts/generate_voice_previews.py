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

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "backend" / "src"))
# The worker's UUID map is the one that drives live calls; import it (the
# module is stdlib-only) instead of carrying a copy that could drift and make
# previews stop matching what callers hear.
sys.path.insert(0, str(_REPO / "worker" / "src"))

from architeq_api.voices import VOICES  # noqa: E402
from architeq_worker.voices import VOICE_UUIDS  # noqa: E402

OUT_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "architeq_api" / "static" / "voice_previews"
)
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
            # Worker keys are provider-prefix-stripped and lowercased
            # ("cartesia-sonic" → "sonic"). Fail loud on a missing mapping
            # rather than falling back to a default voice.
            uuid = VOICE_UUIDS.get(voice_id.removeprefix("cartesia-").lower())
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
                print(
                    f"FAIL {voice_id}: HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr
                )
                failures += 1
                continue
            out.write_bytes(resp.content)
            print(
                f"wrote {out.relative_to(OUT_DIR.parent.parent.parent)} ({len(resp.content)} bytes)"
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
