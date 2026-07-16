"""GET /list-voices and GET /get-voice/{voice_id}."""

import uuid

from tests.conftest import AUTH_HEADERS

VOICE_FIELDS = {
    "voice_id",
    "voice_name",
    "provider",
    "accent",
    "gender",
    "age",
    "preview_audio_url",
}


async def test_list_voices_returns_retell_shaped_catalog(client):
    resp = await client.get("/list-voices", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    voices = resp.json()
    assert len(voices) >= 12
    for voice in voices:
        assert VOICE_FIELDS <= set(voice)
        assert voice["provider"] == "cartesia"
        assert voice["gender"] in ("male", "female")
    ids = {v["voice_id"] for v in voices}
    # Ids referenced elsewhere in the platform must exist in the catalog.
    assert {"cartesia-sonic", "cartesia-sonic-english"} <= ids


async def test_get_voice_by_id(client):
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["voice_id"] == "cartesia-sonic"
    assert body["provider"] == "cartesia"


async def test_get_voice_unknown_id_404(client):
    resp = await client.get("/get-voice/11labs-nope", headers=AUTH_HEADERS)
    assert resp.status_code == 404


async def test_voices_require_auth(client):
    assert (await client.get("/list-voices")).status_code == 401
    assert (await client.get("/get-voice/cartesia-sonic")).status_code == 401


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


async def test_preview_audio_url_null_when_sample_missing(client, tmp_path, monkeypatch):
    from arhiteq_api.api import voices as voices_api

    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)  # empty dir
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert resp.json()["preview_audio_url"] is None


async def test_preview_audio_url_relative_when_sample_exists(client, tmp_path, monkeypatch):
    from arhiteq_api.api import voices as voices_api

    (tmp_path / "cartesia-sonic.mp3").write_bytes(b"ID3 fake mp3")
    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert resp.json()["preview_audio_url"] == "/static/voice_previews/cartesia-sonic.mp3"
    # And the same voice in the list response carries the same URL.
    listed = (await client.get("/list-voices", headers=AUTH_HEADERS)).json()
    sonic = next(v for v in listed if v["voice_id"] == "cartesia-sonic")
    assert sonic["preview_audio_url"] == "/static/voice_previews/cartesia-sonic.mp3"


async def test_preview_audio_url_absolute_with_public_api_url(client, tmp_path, monkeypatch):
    from arhiteq_api.api import voices as voices_api
    from arhiteq_api.config import get_settings

    (tmp_path / "cartesia-sonic.mp3").write_bytes(b"ID3 fake mp3")
    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(get_settings(), "public_api_url", "https://api.example.com/")
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert (
        resp.json()["preview_audio_url"]
        == "https://api.example.com/static/voice_previews/cartesia-sonic.mp3"
    )


async def test_static_mount_serves_preview_files_without_auth(client):
    from arhiteq_api.api.voices import PREVIEWS_DIR

    sample = PREVIEWS_DIR / f"test-sample-{uuid.uuid4().hex}.mp3"
    sample.write_bytes(b"ID3 test bytes")
    try:
        resp = await client.get(f"/static/voice_previews/{sample.name}")
        assert resp.status_code == 200
        assert resp.content == b"ID3 test bytes"
    finally:
        sample.unlink(missing_ok=True)


async def test_preview_audio_url_absolute_without_trailing_slash(client, tmp_path, monkeypatch):
    from arhiteq_api.api import voices as voices_api
    from arhiteq_api.config import get_settings

    (tmp_path / "cartesia-sonic.mp3").write_bytes(b"ID3 fake mp3")
    monkeypatch.setattr(voices_api, "PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(get_settings(), "public_api_url", "https://api.example.com")
    resp = await client.get("/get-voice/cartesia-sonic", headers=AUTH_HEADERS)
    assert (
        resp.json()["preview_audio_url"]
        == "https://api.example.com/static/voice_previews/cartesia-sonic.mp3"
    )
