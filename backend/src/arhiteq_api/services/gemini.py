"""Shared google-genai client construction.

Post-call analysis and the Test-LLM chat both talk to Gemini, and they must
authenticate the same way: Vertex (ADC + GOOGLE_CLOUD_{PROJECT,LOCATION}) when
GOOGLE_GENAI_USE_VERTEXAI is set — as the worker and prod do — otherwise the
Developer API with an API key. Centralized here so the two call sites can't
drift on auth mode (which silently 401s the one that guessed wrong).

Callers pass their own `settings` so the resolved values stay consistent with
whatever the caller already read (and remain patchable in tests).
"""

from typing import Any

from ..config import Settings

# Gemini Live (speech-to-speech) model id markers — kept in lockstep with the
# worker's _LIVE_MODEL_MARKERS and the frontend's LIVE_MODEL_MARKERS. Live
# models are realtime-audio only and can't serve text `generate_content`, so
# text callers (e.g. Test-LLM chat) must not target them.
_LIVE_MODEL_MARKERS = ("native-audio", "-live-", "flash-live", "live-2.5")


def is_live_model(model: str | None) -> bool:
    """True when a wire model id names a Gemini Live (realtime-audio) model."""
    m = (model or "").lower()
    return any(marker in m for marker in _LIVE_MODEL_MARKERS)


def genai_credentials_available(settings: Settings) -> bool:
    """True when either credential path (Vertex ADC or an API key) is usable."""
    return bool(settings.google_genai_use_vertexai or settings.google_api_key)


def build_genai_client(settings: Settings) -> Any:
    """Build a genai.Client honoring GOOGLE_GENAI_USE_VERTEXAI.

    Import is local so modules that only need the availability check (or that
    run in envs without the heavy SDK) don't pay for it at import time.
    """
    from google import genai

    if settings.google_genai_use_vertexai:
        # ADC + GOOGLE_CLOUD_{PROJECT,LOCATION} from env, same as the worker.
        return genai.Client(vertexai=True)
    return genai.Client(api_key=settings.google_api_key)
