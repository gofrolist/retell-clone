"""Resolve Retell-style voice ids to Cartesia voice UUIDs.

Agents store Retell-shaped voice ids ("cartesia-sonic-english",
"11labs-Adrian", ...) — the wire contract keeps them as-is — but the
Cartesia TTS API only accepts voice UUIDs. This module owns the mapping:

- "cartesia-<uuid>" / bare UUID  → used verbatim
- catalog slugs from the control plane's /list-voices → curated UUID
- other provider-prefixed ids ("11labs-Adrian") → same-first-name Cartesia
  voice when we ship one, else the default voice

Every UUID below was verified against the live Cartesia /tts/bytes API.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("arhiteq-worker")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_PROVIDER_PREFIX_RE = re.compile(
    r"^(cartesia|11labs|elevenlabs|openai|play|deepgram|gemini)-", re.IGNORECASE
)

# Sarah — the voice the platform launched with; proven on production calls.
FALLBACK_VOICE_UUID = "694f9389-aac1-45b6-b726-9d9369183238"

# Keys are the id with the provider prefix stripped, lowercased. The first
# block mirrors backend/src/arhiteq_api/voices.py (the dashboard catalog);
# keep the two in sync when adding voices.
VOICE_UUIDS: dict[str, str] = {
    "sonic": FALLBACK_VOICE_UUID,
    "sonic-english": FALLBACK_VOICE_UUID,
    "savannah": "78ab82d5-25be-4f7d-82b3-7ad64e5b85b2",  # Savannah - Magnolia Belle
    "brooke": "e07c00bc-4134-4eae-9ea4-1a55fb45746b",  # Brooke - Big Sister
    "katie": "f786b574-daa5-4673-aa0c-cbe3e8534c02",  # Katie - Friendly Fixer
    "jacqueline": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",  # Jacqueline - Reassuring Agent
    "blake": "a167e0f3-df7e-4d52-a9c3-f949145efdab",  # Blake - Helpful Agent
    "ronald": "5ee9feff-1265-424a-9d7f-8e4d431a12c7",  # Ronald - Thinker
    "connor": "92c41dd4-04aa-45de-8504-a92b40cb8818",  # Connor - Grateful Person
    "griffin": "c99d36f3-5ffd-4253-803a-535c1bc9c306",  # Griffin - Narrator
    "daniela": "5c5ad5e7-1020-476b-8b91-fdcbe9cc313c",  # Daniela - Relaxed Woman
    "luca": "e019ed7e-6079-4467-bc7f-b599a5dccf6f",  # Luca - Everyday Friend
    # Same-name stand-ins for Retell ids seen on imported agents.
    "adrian": "e2d48e7b-cd73-4c4c-bc1e-f232580e8709",  # Adrian - Explorer
    "grace": "c2ad7092-0447-47ea-948b-61fbb6faf153",  # Grace - Helpful Hand
}


def resolve_cartesia_voice(voice_id: str) -> str:
    """Return the Cartesia voice UUID for a Retell-style voice id."""
    name = _PROVIDER_PREFIX_RE.sub("", voice_id or "")
    if _UUID_RE.match(name):
        return name
    mapped = VOICE_UUIDS.get(name.lower())
    if mapped is None:
        # "11labs-Adrian Wise" → try the first name before giving up.
        mapped = VOICE_UUIDS.get(name.split("-")[0].split(" ")[0].lower())
    if mapped is None:
        mapped = os.getenv("ARHITEQ_DEFAULT_CARTESIA_VOICE_ID", FALLBACK_VOICE_UUID)
        logger.warning("voice_id %r has no Cartesia mapping; using default voice", voice_id)
    return mapped


# Gemini Live (speech-to-speech) native-audio prebuilt voices. Unlike Cartesia
# these are not TTS voices with UUIDs — the id maps straight to the voice name
# passed to google.realtime.RealtimeModel(voice=...). Keep in sync with the
# livekit google plugin's api_proto.Voice literal and the dashboard catalog
# (backend/src/arhiteq_api/voices.py, "gemini-*" entries).
GEMINI_LIVE_VOICES: frozenset[str] = frozenset(
    {
        "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede",
        "Autonoe", "Callirrhoe", "Charon", "Despina", "Enceladus", "Erinome",
        "Fenrir", "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus",
        "Pulcherrima", "Puck", "Rasalgethi", "Sadachbia", "Sadaltager",
        "Schedar", "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr",
        "Zubenelgenubi",
    }
)  # fmt: skip

DEFAULT_GEMINI_LIVE_VOICE = "Puck"  # the plugin's own default voice
_GEMINI_VOICE_BY_LOWER = {v.lower(): v for v in GEMINI_LIVE_VOICES}


def resolve_gemini_voice(voice_id: str) -> str:
    """Return the Gemini native-audio voice name for a "gemini-<Voice>" id.

    Unknown ids fall back to the default voice with a warning, mirroring
    resolve_cartesia_voice — a bad voice must never fail the call.
    """
    name = _PROVIDER_PREFIX_RE.sub("", voice_id or "")
    canonical = _GEMINI_VOICE_BY_LOWER.get(name.lower())
    if canonical is None:
        logger.warning(
            "voice_id %r has no Gemini voice mapping; using %s",
            voice_id,
            DEFAULT_GEMINI_LIVE_VOICE,
        )
        return DEFAULT_GEMINI_LIVE_VOICE
    return canonical
