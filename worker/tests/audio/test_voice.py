"""The speech provider calls, tested against a transport that never leaves.

What matters here is not that httpx works. It is that a wrong request still
returns audio and still returns a transcript — the wrong voice, the wrong rate,
yesterday's cached line — and none of that surfaces as an error. So every test
below asserts on what went out on the wire, not just on what came back.
"""

from __future__ import annotations

import io
import json
import wave

import httpx
import pytest

from audio.voice import (
    CALLER_SAMPLE_RATE,
    STT_MODEL,
    TTS_MODEL,
    VoiceError,
    cache_name,
    synthesize,
    transcribe,
)

VOICE = "694f9389-aac1-45b6-b726-9d9369183238"
AUDIO = b"\x01\x02" * 1000


def recorder(handler):
    """A client that records every request it was asked to make."""
    sent: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(capture)), sent


def audio_ok(_request):
    return httpx.Response(200, content=AUDIO)


def text_ok(_request):
    return httpx.Response(200, json={"text": "  Good morning, Margaret.  ", "duration": 2.1})


# --- synthesis -----------------------------------------------------------


def test_the_request_asks_for_raw_pcm_at_the_rate_the_caller_publishes():
    # The published track is declared at one rate. Audio synthesized at another
    # plays back at the wrong speed and pitch, which sounds like a broken
    # caller rather than like a configuration mistake.
    client, sent = recorder(audio_ok)
    synthesize("Morning.", voice=VOICE, api_key="k", client=client)
    body = json.loads(sent[0].read())
    assert body["output_format"] == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": CALLER_SAMPLE_RATE,
    }
    assert body["model_id"] == TTS_MODEL
    assert body["voice"] == {"mode": "id", "id": VOICE}
    assert body["transcript"] == "Morning."


def test_the_api_key_travels_in_the_header_cartesia_reads():
    client, sent = recorder(audio_ok)
    synthesize("Morning.", voice=VOICE, api_key="secret-key", client=client)
    assert sent[0].headers["X-API-Key"] == "secret-key"
    assert sent[0].headers["Cartesia-Version"]


def test_a_refusal_is_raised_with_what_the_provider_said():
    # A 401 that returns b"" reads downstream as a silent caller: the case runs,
    # the agent hears nothing, and the report blames the prompt.
    client, _ = recorder(lambda _r: httpx.Response(401, text="bad key"))
    with pytest.raises(VoiceError, match="401"):
        synthesize("Morning.", voice=VOICE, api_key="k", client=client)


def test_an_empty_body_is_a_failure_not_a_silent_line():
    client, _ = recorder(lambda _r: httpx.Response(200, content=b""))
    with pytest.raises(VoiceError, match="no audio"):
        synthesize("Morning.", voice=VOICE, api_key="k", client=client)


# --- the cache -----------------------------------------------------------


def test_a_cached_line_is_served_without_a_second_request(tmp_path):
    # This is the determinism property, not a saving: sonic-2 does not
    # synthesize a line the same way twice, so an uncached harness feeds the
    # agent different audio every run and a case that flips has two candidate
    # causes instead of one.
    client, sent = recorder(audio_ok)
    first = synthesize("Morning.", voice=VOICE, api_key="k", cache_dir=tmp_path, client=client)
    second = synthesize("Morning.", voice=VOICE, api_key="k", cache_dir=tmp_path, client=client)
    assert first == second == AUDIO
    assert len(sent) == 1


def test_changing_the_voice_does_not_reuse_the_old_recording(tmp_path):
    # Same words in a different voice is different audio. A cache keyed on text
    # alone serves yesterday's voice for today's case, and the change the run
    # was made to observe is invisible.
    client, sent = recorder(audio_ok)
    synthesize("Morning.", voice=VOICE, api_key="k", cache_dir=tmp_path, client=client)
    synthesize("Morning.", voice="another-voice", api_key="k", cache_dir=tmp_path, client=client)
    assert len(sent) == 2


def test_changing_the_rate_does_not_reuse_the_old_recording(tmp_path):
    client, sent = recorder(audio_ok)
    synthesize("Morning.", voice=VOICE, api_key="k", cache_dir=tmp_path, client=client)
    synthesize(
        "Morning.", voice=VOICE, api_key="k", sample_rate=16000, cache_dir=tmp_path, client=client
    )
    assert len(sent) == 2


def test_a_cache_file_can_be_recognised_by_a_person_browsing_the_directory():
    name = cache_name("Not yet, no.", voice=VOICE, model=TTS_MODEL, sample_rate=24000)
    assert name.startswith("not-yet-no-")
    assert name.endswith(".pcm")


def test_a_line_with_no_usable_characters_still_gets_a_filename():
    assert cache_name("?!", voice=VOICE, model=TTS_MODEL, sample_rate=24000).startswith("line-")


# --- transcription -------------------------------------------------------


def test_the_span_is_uploaded_as_a_wav_carrying_its_own_rate():
    # Sending bare samples puts the rate in a query parameter, and a harness
    # that changes its recording rate without changing that parameter gets a
    # transcript that is confidently wrong rather than an error.
    client, sent = recorder(text_ok)
    transcribe(b"\x00\x01" * 8000, api_key="k", sample_rate=16000, client=client)
    body = sent[0].read()
    start = body.index(b"RIFF")
    with wave.open(io.BytesIO(body[start:]), "rb") as parsed:
        assert parsed.getframerate() == 16000
        assert parsed.getnchannels() == 1
    assert STT_MODEL.encode() in body


def test_the_transcript_comes_back_trimmed():
    client, _ = recorder(text_ok)
    assert transcribe(b"\x00\x01" * 100, api_key="k", sample_rate=16000, client=client) == (
        "Good morning, Margaret."
    )


def test_an_empty_span_costs_no_request():
    client, sent = recorder(text_ok)
    assert transcribe(b"", api_key="k", sample_rate=16000, client=client) == ""
    assert sent == []


def test_a_span_with_no_words_in_it_is_an_empty_string_not_a_crash():
    client, _ = recorder(lambda _r: httpx.Response(200, json={"text": None}))
    assert transcribe(b"\x00\x01" * 100, api_key="k", sample_rate=16000, client=client) == ""


def test_a_transcription_refusal_is_raised():
    client, _ = recorder(lambda _r: httpx.Response(500, text="upstream"))
    with pytest.raises(VoiceError, match="500"):
        transcribe(b"\x00\x01" * 100, api_key="k", sample_rate=16000, client=client)
