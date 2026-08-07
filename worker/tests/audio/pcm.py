"""Turning received audio into a timeline the rules can be run against.

Stdlib only, like `analysis.py` and for the same reason: the worker's CI job
installs the dev group alone, so anything importing `livekit` cannot run there.
Everything that decides *where a segment starts and stops* lives in this file
rather than in the caller bot, because that decision is the one most likely to
be wrong and the one least likely to be noticed — a segmenter that is 300ms out
still produces a plausible-looking report.

Two jobs:

- **Keeping the clock honest.** `Recording` is fed frames as they arrive off
  the wire and produces one continuous buffer, so sample N is always the same
  moment of the call no matter how the network behaved.
- **Finding the speech.** `speech_spans` marks where the agent was talking.
  Offline, over the finished buffer, so the same recording always segments the
  same way — the harness has enough non-determinism in it already.
"""

from __future__ import annotations

import array
import io
import math
import wave

# The agent's track is recorded at 16 kHz mono: what STT wants, small enough to
# keep every failing call's WAV around, and far more than a voice needs.
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # signed 16-bit, the only width anything here handles
CHANNELS = 1

SILENCE = b"\x00\x00"

# How far the buffer may drift behind the wall clock before the gap is treated
# as real silence rather than as jitter.
#
# Frames arrive in bursts — a jitter buffer releasing, an event loop that was
# busy elsewhere — so a small deficit is normal and padding it would scatter
# clicks through the recording. A deficit this large is not jitter: it is the
# track having gone quiet.
DEFAULT_MAX_DRIFT_S = 0.2

# --- speech detection ----------------------------------------------------

# RMS is computed over windows this long. Short enough to place a boundary
# within a syllable, long enough that one loud sample cannot open a span.
WINDOW_S = 0.02

# Anything below this is silence however quiet the rest of the call is. About
# -44 dBFS: comfort noise and codec hiss sit well under it, speech well over.
ABSOLUTE_FLOOR = 150.0

# Speech has to be this much louder than the room's own noise floor.
NOISE_MULTIPLE = 4.0

# A span must be at least this long to be speech. Below it, a click on track
# subscription or a single codec artefact would become an utterance.
MIN_SPEECH_S = 0.20

# Silence shorter than this does not end a span.
#
# This is the value that decides whether one sentence stays one segment. TTS
# leaves ~0.4s between sentences of the same turn, and splitting there would
# chop a greeting into two segments that each fall under the duplicate rule's
# four-word minimum — losing the bug the rule exists for. Longer than any pause
# inside a turn, shorter than the gap between turns.
MIN_SILENCE_S = 0.6


def write_wav(pcm: bytes, path, *, sample_rate: int = SAMPLE_RATE) -> None:
    """The buffer as a file someone can double-click and listen to.

    That is the whole point of keeping it: a failure hands you the two seconds
    that broke instead of asking you to place the call again and hope.
    """
    with wave.open(str(path), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(sample_rate)
        out.writeframes(pcm)


def read_wav(path) -> tuple[bytes, int]:
    """A 16-bit mono WAV back as `(pcm, sample_rate)`.

    Anything else raises rather than being coerced. A stereo or 8-bit file read
    as if it were 16-bit mono still produces samples, still segments, and still
    reports findings — all at the wrong times.
    """
    with wave.open(str(path), "rb") as src:
        if src.getsampwidth() != SAMPLE_WIDTH or src.getnchannels() != CHANNELS:
            raise ValueError(
                f"{path} is {src.getnchannels()}ch/{src.getsampwidth() * 8}-bit; "
                "this harness only handles 16-bit mono"
            )
        return src.readframes(src.getnframes()), src.getframerate()


def as_wav_bytes(pcm: bytes, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """The buffer as a WAV in memory, for uploading to a speech API."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def duration_s(pcm: bytes, *, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / SAMPLE_WIDTH / sample_rate


def slice_pcm(pcm: bytes, start: float, end: float, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """The samples between two times, clamped to what exists."""
    first = max(0, int(start * sample_rate)) * SAMPLE_WIDTH
    last = min(len(pcm), max(0, int(end * sample_rate)) * SAMPLE_WIDTH)
    return pcm[first:last] if last > first else b""


class Recording:
    """One party's audio, assembled into a continuous timeline.

    Fed `(elapsed, frame)` as frames arrive, where `elapsed` is seconds since
    the recording started. The frames themselves carry no absolute time, so a
    naive `buffer += frame` produces audio whose length is the amount of sound
    that arrived rather than the length of the call — and every timestamp the
    rules report is then shifted by however long the track was quiet. On a call
    where the agent froze for eight seconds, the freeze simply is not in the
    file.

    So a frame that arrives later than the buffer has audio for has that gap
    filled with silence. The reverse — frames arriving faster than real time,
    which is what a jitter buffer flushing looks like — is left alone: the
    samples are real audio and dropping them to fit the clock would be worse
    than the few tens of milliseconds of drift it saves.
    """

    def __init__(self, *, sample_rate: int = SAMPLE_RATE, max_drift_s: float = DEFAULT_MAX_DRIFT_S):
        self.sample_rate = sample_rate
        self.max_drift_s = max_drift_s
        self._chunks: list[bytes] = []
        self._samples = 0
        self.padded_s = 0.0

    @property
    def duration(self) -> float:
        """Seconds of audio held, which is also the position of the next frame."""
        return self._samples / self.sample_rate

    @property
    def pcm(self) -> bytes:
        return b"".join(self._chunks)

    def add(self, elapsed: float, frame: bytes) -> None:
        deficit = elapsed - self.duration
        if deficit > self.max_drift_s:
            missing = int(deficit * self.sample_rate)
            self._chunks.append(SILENCE * missing)
            self._samples += missing
            self.padded_s += deficit
        self._chunks.append(frame)
        self._samples += len(frame) // SAMPLE_WIDTH

    def pad_to(self, elapsed: float) -> None:
        """Silence out to `elapsed`, for the quiet at the end of a call.

        Without this a call that ends while the agent is silent ends the
        recording at the last frame that arrived, and `call_end` — the boundary
        that makes a frozen agent visible at all — comes out short.
        """
        deficit = elapsed - self.duration
        if deficit <= 0:
            return
        missing = int(deficit * self.sample_rate)
        self._chunks.append(SILENCE * missing)
        self._samples += missing
        self.padded_s += deficit


def window_rms(
    pcm: bytes, *, sample_rate: int = SAMPLE_RATE, window_s: float = WINDOW_S
) -> list[float]:
    """Loudness per window, as plain RMS over the 16-bit samples."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % SAMPLE_WIDTH])
    size = max(1, int(window_s * sample_rate))
    levels = []
    for start in range(0, len(samples) - size + 1, size):
        window = samples[start : start + size]
        levels.append(math.sqrt(sum(s * s for s in window) / size))
    return levels


def speech_threshold(levels: list[float]) -> float:
    """The line between silence and speech for one recording.

    Adaptive rather than fixed, because the noise floor of a call is a property
    of the codec and the path, not something to hard-code — but adaptive with
    both ends nailed down:

    - never below `ABSOLUTE_FLOOR`, so a pristine recording whose quiet parts
      are digital silence does not end up with a threshold near zero and every
      codec artefact as an utterance;
    - never above half the loud end, so a recording that is mostly speech —
      which is what a short one-turn case is — cannot mistake speech for its
      own noise floor and threshold itself down to nothing found.
    """
    if not levels:
        return ABSOLUTE_FLOOR
    ordered = sorted(levels)
    noise = ordered[len(ordered) // 10]
    loud = ordered[min(len(ordered) - 1, (len(ordered) * 9) // 10)]
    return min(max(ABSOLUTE_FLOOR, noise * NOISE_MULTIPLE), max(ABSOLUTE_FLOOR, loud / 2))


def speech_spans(
    pcm: bytes,
    *,
    sample_rate: int = SAMPLE_RATE,
    window_s: float = WINDOW_S,
    min_speech_s: float = MIN_SPEECH_S,
    min_silence_s: float = MIN_SILENCE_S,
    threshold: float | None = None,
) -> list[tuple[float, float]]:
    """Where the speaker was talking, as `(start, end)` seconds.

    Run over the finished buffer rather than live, so a recording always
    segments into the same spans. A harness whose segment boundaries move
    between runs of the same audio cannot tell a prompt regression from its own
    noise.
    """
    levels = window_rms(pcm, sample_rate=sample_rate, window_s=window_s)
    if not levels:
        return []
    line = speech_threshold(levels) if threshold is None else threshold
    hangover = max(1, round(min_silence_s / window_s))

    spans: list[tuple[float, float]] = []
    start: int | None = None
    quiet = 0
    for index, level in enumerate(levels):
        if level >= line:
            if start is None:
                start = index
            quiet = 0
            continue
        if start is None:
            continue
        quiet += 1
        if quiet >= hangover:
            spans.append((start, index - quiet + 1))
            start = None
            quiet = 0
    if start is not None:
        spans.append((start, len(levels)))

    return [
        (first * window_s, last * window_s)
        for first, last in spans
        if (last - first) * window_s >= min_speech_s
    ]
