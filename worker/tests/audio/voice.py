"""Speaking the caller's lines, and reading back what the agent said.

Both directions go to Cartesia over plain HTTP rather than through the livekit
plugins, for two reasons. The plugins are streaming interfaces built for a live
session — this harness wants a whole line as bytes before the call starts and a
whole span as text after it ends. And httpx is in the worker's dev group while
`livekit-agents` is not, so keeping these here means they are covered by the
existing CI job.

The provider is the same one the worker itself uses (`sonic-2` out,
`ink-whisper` back), so one `CARTESIA_API_KEY` covers the harness and there is
no second vendor to keep credentials for.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

from audio.pcm import as_wav_bytes

CARTESIA_BASE = "https://api.cartesia.ai"
CARTESIA_VERSION = "2025-04-16"

TTS_MODEL = "sonic-2"
STT_MODEL = "ink-whisper"

# The caller is synthesized at 24 kHz and published at that rate; the agent's
# track is recorded at 16 kHz for STT. The two never meet, so neither is
# resampled and neither has to compromise for the other.
CALLER_SAMPLE_RATE = 24000

# Long enough for a slow synthesis of a long line, short enough that a hung
# provider fails the run instead of holding a LiveKit room open indefinitely.
TIMEOUT_S = 60.0

_SLUG = re.compile(r"[^a-z0-9]+")


class VoiceError(RuntimeError):
    """The speech provider refused, with enough detail to act on."""


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "Cartesia-Version": CARTESIA_VERSION}


def _check(response: httpx.Response, what: str) -> httpx.Response:
    if response.status_code >= 400:
        raise VoiceError(f"{what} failed: HTTP {response.status_code} {response.text[:400]}")
    return response


def cache_name(text: str, *, voice: str, model: str, sample_rate: int) -> str:
    """A filename that changes when anything about the audio would change.

    The voice and the model are in the key, not just the text: a line
    re-synthesized in a different voice is different audio, and a cache that
    ignored that would serve yesterday's voice for today's case and make the
    difference invisible.
    """
    digest = hashlib.sha256(
        "\x00".join([text, voice, model, str(sample_rate)]).encode()
    ).hexdigest()[:12]
    slug = _SLUG.sub("-", text.lower()).strip("-")[:40] or "line"
    return f"{slug}-{digest}.pcm"


def synthesize(
    text: str,
    *,
    voice: str,
    api_key: str,
    model: str = TTS_MODEL,
    sample_rate: int = CALLER_SAMPLE_RATE,
    language: str = "en",
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> bytes:
    """One caller line as raw 16-bit PCM.

    Cached on disk, and that is a correctness property rather than a saving.
    `sonic-2` does not synthesize the same line the same way twice — timing and
    emphasis move between requests — so an uncached harness feeds the agent
    slightly different audio on every run, and a case that flips has two
    candidate causes instead of one. Cached, the caller says exactly the same
    thing every time and the prompt is the only thing that moved.
    """
    path = (
        cache_dir / cache_name(text, voice=voice, model=model, sample_rate=sample_rate)
        if cache_dir
        else None
    )
    if path is not None and path.is_file():
        return path.read_bytes()

    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = _check(
            http.post(
                f"{CARTESIA_BASE}/tts/bytes",
                headers=_headers(api_key),
                json={
                    "model_id": model,
                    "transcript": text,
                    "voice": {"mode": "id", "id": voice},
                    "language": language,
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_s16le",
                        "sample_rate": sample_rate,
                    },
                },
            ),
            f"synthesizing {text!r}",
        )
    finally:
        if owned:
            http.close()

    pcm = response.content
    if not pcm:
        raise VoiceError(f"synthesizing {text!r} returned no audio")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pcm)
    return pcm


def transcribe(
    pcm: bytes,
    *,
    api_key: str,
    sample_rate: int,
    model: str = STT_MODEL,
    language: str = "en",
    client: httpx.Client | None = None,
) -> str:
    """What the agent said in one span of the recording.

    Uploaded as a WAV rather than as raw samples so the file carries its own
    rate. Sending bare PCM means the rate travels in a query parameter, and a
    harness that changes its recording rate without changing that parameter
    gets a transcript that is confidently wrong rather than an error.

    An empty span transcribes to an empty string without a request: the
    segmenter can hand over something under a window long, and paying a round
    trip to be told there are no words in it is waste.
    """
    if not pcm:
        return ""

    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = _check(
            http.post(
                f"{CARTESIA_BASE}/stt",
                headers=_headers(api_key),
                files={
                    "file": ("span.wav", as_wav_bytes(pcm, sample_rate=sample_rate), "audio/wav")
                },
                data={"model": model, "language": language},
            ),
            "transcribing",
        )
    finally:
        if owned:
            http.close()

    return (response.json().get("text") or "").strip()
