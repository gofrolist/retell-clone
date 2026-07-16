"""Voice-id → Cartesia UUID resolution."""

from arhiteq_worker.voices import (
    FALLBACK_VOICE_UUID,
    VOICE_UUIDS,
    resolve_cartesia_voice,
)

SARAH = "694f9389-aac1-45b6-b726-9d9369183238"
KATIE = "f786b574-daa5-4673-aa0c-cbe3e8534c02"


def test_cartesia_prefixed_uuid_passes_through():
    assert resolve_cartesia_voice(f"cartesia-{SARAH}") == SARAH


def test_bare_uuid_passes_through():
    assert resolve_cartesia_voice(SARAH) == SARAH


def test_uuid_passthrough_is_case_insensitive():
    assert resolve_cartesia_voice(SARAH.upper()) == SARAH.upper()


def test_catalog_slugs_resolve():
    assert resolve_cartesia_voice("cartesia-sonic-english") == SARAH
    assert resolve_cartesia_voice("cartesia-katie") == KATIE


def test_catalog_slug_lookup_is_case_insensitive():
    assert resolve_cartesia_voice("cartesia-Katie") == KATIE


def test_11labs_name_maps_to_same_name_cartesia_voice():
    assert resolve_cartesia_voice("11labs-Adrian") == VOICE_UUIDS["adrian"]


def test_compound_name_falls_back_to_first_name():
    assert resolve_cartesia_voice("11labs-Adrian Wise") == VOICE_UUIDS["adrian"]


def test_unknown_voice_falls_back_to_default():
    assert resolve_cartesia_voice("11labs-Nonexistent") == FALLBACK_VOICE_UUID
    assert resolve_cartesia_voice("") == FALLBACK_VOICE_UUID


def test_unknown_voice_honours_env_default(monkeypatch):
    monkeypatch.setenv("ARHITEQ_DEFAULT_CARTESIA_VOICE_ID", KATIE)
    assert resolve_cartesia_voice("openai-alloy") == KATIE


def test_all_mapped_uuids_are_well_formed():
    import re

    uuid_re = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    for name, uuid in VOICE_UUIDS.items():
        assert uuid_re.match(uuid), name
