"""Static curated voice catalog, shaped after Retell's voice object.

The worker synthesizes with Cartesia; this catalog mirrors the Cartesia
voices we ship. `preview_audio_url` is null until previews are hosted.
`recommended` marks the voices surfaced as cards in the dashboard's voice picker.
"""

from typing import Any

VOICES: list[dict[str, Any]] = [
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
    {
        "voice_id": "cartesia-sonic-english",
        "voice_name": "Sonic English",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-savannah",
        "voice_name": "Savannah",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Middle Aged",
        "preview_audio_url": None,
        "recommended": True,
    },
    {
        "voice_id": "cartesia-brooke",
        "voice_name": "Brooke",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-katie",
        "voice_name": "Katie",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-jacqueline",
        "voice_name": "Jacqueline",
        "provider": "cartesia",
        "accent": "British",
        "gender": "female",
        "age": "Middle Aged",
        "preview_audio_url": None,
        "recommended": True,
    },
    {
        "voice_id": "cartesia-blake",
        "voice_name": "Blake",
        "provider": "cartesia",
        "accent": "American",
        "gender": "male",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": True,
    },
    {
        "voice_id": "cartesia-ronald",
        "voice_name": "Ronald",
        "provider": "cartesia",
        "accent": "American",
        "gender": "male",
        "age": "Middle Aged",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-connor",
        "voice_name": "Connor",
        "provider": "cartesia",
        "accent": "American",
        "gender": "male",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-griffin",
        "voice_name": "Griffin",
        "provider": "cartesia",
        "accent": "British",
        "gender": "male",
        "age": "Old",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-daniela",
        "voice_name": "Daniela",
        "provider": "cartesia",
        "accent": "Spanish",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
        "recommended": False,
    },
    {
        "voice_id": "cartesia-luca",
        "voice_name": "Luca",
        "provider": "cartesia",
        "accent": "German",
        "gender": "male",
        "age": "Middle Aged",
        "preview_audio_url": None,
        "recommended": False,
    },
]

# Gemini Live (speech-to-speech) native-audio voices. Unlike the Cartesia
# voices above, these have no UUID — the id maps straight to the voice name the
# worker passes to google.realtime.RealtimeModel (see worker/src/
# arhiteq_worker/voices.py, resolve_gemini_voice). Only usable with a Gemini
# Live model; the dashboard couples the LLM choice and the voice tab.
# `description` is Google's published one-word voice characteristic
# (https://ai.google.dev/gemini-api/docs/speech-generation#voices).
_GEMINI_VOICES: list[tuple[str, str]] = [
    ("Puck", "Upbeat"),
    ("Charon", "Informative"),
    ("Kore", "Firm"),
    ("Fenrir", "Excitable"),
    ("Aoede", "Breezy"),
    ("Leda", "Youthful"),
    ("Orus", "Firm"),
    ("Zephyr", "Bright"),
    ("Callirrhoe", "Easy-going"),
    ("Autonoe", "Bright"),
    ("Enceladus", "Breathy"),
    ("Iapetus", "Clear"),
    ("Umbriel", "Easy-going"),
    ("Algieba", "Smooth"),
    ("Despina", "Smooth"),
    ("Erinome", "Clear"),
    ("Algenib", "Gravelly"),
    ("Rasalgethi", "Informative"),
    ("Laomedeia", "Upbeat"),
    ("Achernar", "Soft"),
    ("Alnilam", "Firm"),
    ("Schedar", "Even"),
    ("Gacrux", "Mature"),
    ("Pulcherrima", "Forward"),
    ("Achird", "Friendly"),
    ("Zubenelgenubi", "Casual"),
    ("Vindemiatrix", "Gentle"),
    ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"),
    ("Sulafat", "Warm"),
]
_GEMINI_RECOMMENDED = {"Puck", "Kore", "Charon", "Aoede", "Leda", "Zephyr"}

VOICES += [
    {
        "voice_id": f"gemini-{name}",
        "voice_name": name,
        "provider": "gemini",
        "accent": None,
        "gender": None,
        "age": None,
        "description": trait,
        "preview_audio_url": None,
        "recommended": name in _GEMINI_RECOMMENDED,
    }
    for name, trait in _GEMINI_VOICES
]

VOICES_BY_ID: dict[str, dict[str, Any]] = {v["voice_id"]: v for v in VOICES}
