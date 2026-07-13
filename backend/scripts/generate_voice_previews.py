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
