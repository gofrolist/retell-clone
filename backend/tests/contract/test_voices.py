"""GET /list-voices and GET /get-voice/{voice_id}."""

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
