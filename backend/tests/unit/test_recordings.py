"""sign_recording_url: GCS URLs get signed, everything else passes through."""

import pytest

from architeq_api.services import recordings


@pytest.mark.asyncio
async def test_none_and_empty_pass_through():
    assert await recordings.sign_recording_url(None) is None
    assert await recordings.sign_recording_url("") == ""


@pytest.mark.asyncio
async def test_non_gcs_url_passes_through():
    url = "https://storage.example/rec.mp3"
    assert await recordings.sign_recording_url(url) == url


@pytest.mark.asyncio
async def test_already_signed_url_passes_through():
    url = "https://storage.googleapis.com/b/calls/c.ogg?X-Goog-Signature=abc"
    assert await recordings.sign_recording_url(url) == url


@pytest.mark.asyncio
async def test_gcs_url_is_signed(monkeypatch):
    seen = {}

    def fake_sign(bucket, obj):
        seen["args"] = (bucket, obj)
        return "https://storage.googleapis.com/b/calls/c.ogg?X-Goog-Signature=signed"

    monkeypatch.setattr(recordings, "_sign", fake_sign)
    out = await recordings.sign_recording_url("https://storage.googleapis.com/b/calls/c.ogg")
    assert out.endswith("X-Goog-Signature=signed")
    assert seen["args"] == ("b", "calls/c.ogg")


@pytest.mark.asyncio
async def test_signing_failure_keeps_original(monkeypatch):
    def boom(bucket, obj):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(recordings, "_sign", boom)
    url = "https://storage.googleapis.com/b/calls/c.ogg"
    assert await recordings.sign_recording_url(url) == url


def test_ttl_clamped_to_v4_max(monkeypatch):
    monkeypatch.setenv("ARCHITEQ_RECORDING_URL_TTL_SECONDS", str(30 * 24 * 3600))
    assert recordings._ttl().total_seconds() == 7 * 24 * 3600
    monkeypatch.setenv("ARCHITEQ_RECORDING_URL_TTL_SECONDS", "3600")
    assert recordings._ttl().total_seconds() == 3600
