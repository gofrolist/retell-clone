"""The timeline and the segmenter, tested without a network or a call.

Every case is a way the recording can come out looking plausible and being
wrong — audio that is all there but at the wrong times, a turn split into two
segments, a click promoted to an utterance. Those do not announce themselves in
a report; they just move every timestamp in it.
"""

from __future__ import annotations

import array
import math
import wave

import pytest

from audio.pcm import (
    ABSOLUTE_FLOOR,
    SAMPLE_RATE,
    Recording,
    as_wav_bytes,
    chunk_pcm,
    duration_s,
    read_wav,
    rms,
    slice_pcm,
    speech_spans,
    speech_threshold,
    window_rms,
    write_wav,
)


def tone(seconds: float, amplitude: int = 8000, hz: float = 300.0) -> bytes:
    """A steady tone, standing in for speech. RMS is amplitude / sqrt(2)."""
    count = int(seconds * SAMPLE_RATE)
    samples = array.array(
        "h",
        (int(amplitude * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)) for i in range(count)),
    )
    return samples.tobytes()


def hush(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * SAMPLE_RATE)


# --- the timeline --------------------------------------------------------


def test_frames_arriving_on_time_are_simply_joined():
    rec = Recording()
    rec.add(0.0, tone(0.1))
    rec.add(0.1, tone(0.1))
    assert rec.duration == pytest.approx(0.2)
    assert rec.padded_s == 0.0


def test_a_freeze_is_padded_so_later_audio_lands_at_the_right_time():
    # The bug this exists for: the agent goes quiet for eight seconds, no frames
    # arrive, and a naive buffer ends up eight seconds shorter than the call.
    # Every timestamp after the freeze is then wrong, and the freeze itself is
    # not in the file at all — so the WAV kept as evidence does not contain the
    # thing it was kept for.
    rec = Recording()
    rec.add(0.0, tone(0.5))
    rec.add(8.5, tone(0.5))
    assert rec.duration == pytest.approx(9.0, abs=0.01)
    assert rec.padded_s == pytest.approx(8.0, abs=0.01)


def test_ordinary_jitter_is_not_padded():
    # Frames arrive in bursts as the jitter buffer releases. Padding a 60ms
    # deficit would scatter clicks through every recording the harness makes.
    rec = Recording()
    rec.add(0.0, tone(0.1))
    rec.add(0.16, tone(0.1))
    assert rec.padded_s == 0.0
    assert rec.duration == pytest.approx(0.2)


def test_frames_arriving_ahead_of_the_clock_are_kept_whole():
    # A flushing jitter buffer delivers real audio faster than real time.
    # Trimming it to fit the clock would delete speech to save 40ms of drift.
    rec = Recording()
    rec.add(0.0, tone(0.2))
    rec.add(0.05, tone(0.2))
    assert rec.duration == pytest.approx(0.4)


def test_padding_to_the_end_of_the_call_keeps_the_final_silence():
    # A call that ends while the agent is quiet ends the recording at the last
    # frame that arrived — and `call_end`, the boundary that makes a frozen
    # agent visible at all, comes out short.
    rec = Recording()
    rec.add(0.0, tone(1.0))
    rec.pad_to(12.0)
    assert rec.duration == pytest.approx(12.0, abs=0.01)


def test_padding_to_the_end_never_shortens_what_was_recorded():
    rec = Recording()
    rec.add(0.0, tone(5.0))
    rec.pad_to(2.0)
    assert rec.duration == pytest.approx(5.0)


# --- files ---------------------------------------------------------------


def test_a_wav_round_trips(tmp_path):
    pcm = tone(0.25)
    path = tmp_path / "call.wav"
    write_wav(pcm, path)
    back, rate = read_wav(path)
    assert back == pcm
    assert rate == SAMPLE_RATE


def test_reading_refuses_a_file_this_harness_would_misread(tmp_path):
    # Stereo read as if it were mono still produces samples, still segments and
    # still reports findings — at half the right times, silently.
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(tone(0.1) * 2)
    with pytest.raises(ValueError, match="16-bit mono"):
        read_wav(path)


def test_wav_bytes_carry_the_rate_a_speech_api_will_read(tmp_path):
    data = as_wav_bytes(tone(0.1), sample_rate=8000)
    path = tmp_path / "in-memory.wav"
    path.write_bytes(data)
    with wave.open(str(path), "rb") as src:
        assert src.getframerate() == 8000
        assert src.getnchannels() == 1


def test_duration_and_slicing_agree_with_each_other():
    pcm = tone(3.0)
    assert duration_s(pcm) == pytest.approx(3.0)
    assert duration_s(slice_pcm(pcm, 1.0, 2.0)) == pytest.approx(1.0)


def test_chunking_never_splits_a_sample():
    # A boundary that lands mid-sample shifts every following byte by one and
    # turns the rest of the line into noise — which sounds like a broken
    # microphone rather than like an off-by-one, and gets debugged from the
    # wrong end.
    chunks = chunk_pcm(tone(0.1), sample_rate=SAMPLE_RATE, chunk_s=0.02)
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    assert b"".join(chunks) == tone(0.1)


def test_chunking_leaves_the_last_piece_short_rather_than_padding_it():
    # Padding would add silence the caller did not say to the end of every line.
    chunks = chunk_pcm(tone(0.05), sample_rate=SAMPLE_RATE, chunk_s=0.02)
    assert len(chunks) == 3
    assert len(chunks[-1]) < len(chunks[0])


def test_chunking_drops_a_trailing_half_sample_rather_than_shipping_it():
    chunks = chunk_pcm(b"\x01\x02\x03", sample_rate=SAMPLE_RATE, chunk_s=0.02)
    assert b"".join(chunks) == b"\x01\x02"


def test_chunking_nothing_is_no_chunks():
    assert chunk_pcm(b"", sample_rate=SAMPLE_RATE, chunk_s=0.02) == []


def test_slicing_past_the_end_clamps_instead_of_wrapping():
    pcm = tone(1.0)
    assert duration_s(slice_pcm(pcm, 0.5, 9.0)) == pytest.approx(0.5)
    assert slice_pcm(pcm, 5.0, 9.0) == b""
    assert slice_pcm(pcm, -2.0, 0.1) == pcm[: int(0.1 * SAMPLE_RATE) * 2]


# --- the threshold -------------------------------------------------------


def test_a_clean_recording_does_not_threshold_itself_down_to_hiss():
    # Quiet parts of a clean call are near digital silence. A purely
    # proportional threshold lands near zero there and promotes every codec
    # artefact to an utterance.
    levels = [1.0] * 90 + [4000.0] * 10
    assert speech_threshold(levels) >= ABSOLUTE_FLOOR


def test_a_recording_that_is_mostly_speech_still_finds_the_speech():
    # A one-turn case is mostly the agent talking, so the "quiet" tenth of it is
    # speech. Left uncapped, the threshold is set from that and lands four times
    # above the loudest thing in the file: nothing found, and a green report on
    # a call nobody listened to.
    levels = [3000.0] * 100
    assert speech_threshold(levels) < 3000.0


def test_the_threshold_lands_between_the_noise_and_the_speech():
    levels = [400.0] * 60 + [5000.0] * 40
    line = speech_threshold(levels)
    assert 400.0 < line < 5000.0


def test_a_single_wire_frame_can_be_measured():
    # LiveKit delivers 10ms frames. The windows are 20ms, so window_rms sees
    # nothing in any of them and returns an empty list — no error, no levels.
    # The first real call spent 25 seconds waiting on every turn because of
    # exactly this: the caller concluded the agent had never spoken.
    frame = tone(0.01, amplitude=10000)
    assert window_rms(frame) == []
    assert rms(frame) == pytest.approx(10000 / math.sqrt(2), rel=0.1)


def test_measuring_nothing_is_silence_not_a_crash():
    assert rms(b"") == 0.0
    assert rms(b"\x01") == 0.0


def test_a_frame_of_digital_silence_reads_as_silent():
    assert rms(hush(0.01)) == 0.0


def test_window_rms_measures_what_it_should():
    # RMS of a sine is its amplitude over root two; if this drifts, every
    # threshold constant above is calibrated against the wrong scale.
    levels = window_rms(tone(0.2, amplitude=10000))
    assert levels[2] == pytest.approx(10000 / math.sqrt(2), rel=0.05)


# --- the segmenter -------------------------------------------------------


def test_one_utterance_is_one_span_at_the_right_times():
    pcm = hush(0.5) + tone(1.0) + hush(1.0)
    spans = speech_spans(pcm)
    assert len(spans) == 1
    start, end = spans[0]
    assert start == pytest.approx(0.5, abs=0.05)
    assert end == pytest.approx(1.5, abs=0.05)


def test_the_pause_between_two_sentences_does_not_split_a_turn():
    # TTS leaves ~0.4s between sentences. Splitting there chops a greeting into
    # two segments that each fall under the duplicate rule's four-word minimum,
    # which loses the doubled-greeting bug the rule exists for.
    pcm = tone(1.0) + hush(0.4) + tone(1.0)
    assert len(speech_spans(pcm)) == 1


def test_the_gap_between_two_turns_does_split_them():
    pcm = tone(1.0) + hush(1.5) + tone(1.0)
    spans = speech_spans(pcm)
    assert len(spans) == 2
    assert spans[0][1] == pytest.approx(1.0, abs=0.05)
    assert spans[1][0] == pytest.approx(2.5, abs=0.05)


def test_a_click_is_not_an_utterance():
    # Track subscription and codec artefacts produce short loud transients. A
    # segmenter that calls them speech sends them to STT, which returns
    # something, and the report gains an utterance nobody said.
    pcm = hush(0.5) + tone(0.05) + hush(1.0)
    assert speech_spans(pcm) == []


def test_digital_silence_produces_nothing():
    assert speech_spans(hush(3.0)) == []


def test_line_noise_alone_is_not_speech():
    pcm = tone(3.0, amplitude=70)
    assert speech_spans(pcm) == []


def test_speech_over_line_noise_is_found():
    quiet_line = tone(1.0, amplitude=70)
    pcm = quiet_line + tone(1.0, amplitude=8000) + quiet_line
    spans = speech_spans(pcm)
    assert len(spans) == 1
    assert spans[0][0] == pytest.approx(1.0, abs=0.05)


def test_speech_running_to_the_end_of_the_recording_is_still_a_span():
    # The agent talking as the recording stops is common — a case hangs up on
    # its last line. Requiring a trailing silence to close a span would drop it.
    spans = speech_spans(hush(0.5) + tone(1.0))
    assert len(spans) == 1
    assert spans[0][1] == pytest.approx(1.5, abs=0.05)


def test_segmenting_the_same_audio_twice_gives_the_same_spans():
    # Run offline over the finished buffer for exactly this reason: a harness
    # whose boundaries move between runs cannot tell a regression from itself.
    pcm = hush(0.3) + tone(0.8) + hush(1.2) + tone(0.6)
    assert speech_spans(pcm) == speech_spans(pcm)
