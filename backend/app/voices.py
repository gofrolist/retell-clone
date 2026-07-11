"""Static curated voice catalog, shaped after Retell's voice object.

The worker synthesizes with Cartesia; this catalog mirrors the Cartesia
voices we ship. `preview_audio_url` is null until previews are hosted.
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
    },
    {
        "voice_id": "cartesia-sonic-english",
        "voice_name": "Sonic English",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-savannah",
        "voice_name": "Savannah",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Middle Aged",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-brooke",
        "voice_name": "Brooke",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-katie",
        "voice_name": "Katie",
        "provider": "cartesia",
        "accent": "American",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-jacqueline",
        "voice_name": "Jacqueline",
        "provider": "cartesia",
        "accent": "British",
        "gender": "female",
        "age": "Middle Aged",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-blake",
        "voice_name": "Blake",
        "provider": "cartesia",
        "accent": "American",
        "gender": "male",
        "age": "Young",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-ronald",
        "voice_name": "Ronald",
        "provider": "cartesia",
        "accent": "American",
        "gender": "male",
        "age": "Middle Aged",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-connor",
        "voice_name": "Connor",
        "provider": "cartesia",
        "accent": "American",
        "gender": "male",
        "age": "Young",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-griffin",
        "voice_name": "Griffin",
        "provider": "cartesia",
        "accent": "British",
        "gender": "male",
        "age": "Old",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-daniela",
        "voice_name": "Daniela",
        "provider": "cartesia",
        "accent": "Spanish",
        "gender": "female",
        "age": "Young",
        "preview_audio_url": None,
    },
    {
        "voice_id": "cartesia-luca",
        "voice_name": "Luca",
        "provider": "cartesia",
        "accent": "German",
        "gender": "male",
        "age": "Middle Aged",
        "preview_audio_url": None,
    },
]

VOICES_BY_ID: dict[str, dict[str, Any]] = {v["voice_id"]: v for v in VOICES}
