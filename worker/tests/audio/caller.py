"""A caller bot: joins the call's room, says its lines, records what it hears.

This is the only file in the layer that imports `livekit`, and that is
deliberate. The worker's CI job installs the dev group alone, so anything here
cannot run there — everything that makes a *judgement* lives in `pcm.py` and
`analysis.py`, which are stdlib-only and covered. What is left here is
plumbing: connect, publish, subscribe, wait.

Two clocks that must not be confused:

- **Turn-taking** uses a live, fixed loudness floor, frame by frame, because a
  decision about when to speak has to be made before the recording exists.
- **The report** uses `pcm.speech_spans` offline over the finished buffer, with
  a threshold adapted to that recording. That one has to be reproducible; this
  one only has to be roughly right, and being roughly right about when to talk
  is what a caller does anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from livekit import rtc

from audio.pcm import SAMPLE_RATE, Recording, chunk_pcm, rms
from audio.verdict import room_close_ended_the_call

# The rate the caller's lines were synthesized at, taken from the module that
# synthesizes them rather than restated here. Publishing at a rate the audio was
# not made at plays it back at the wrong speed and pitch, which sounds like a
# broken caller rather than like two constants that drifted apart.
from audio.voice import CALLER_SAMPLE_RATE as CALLER_RATE

log = logging.getLogger("audio-caller")

CALLER_CHANNELS = 1

# Audio is pushed to LiveKit in chunks this long. Short enough that
# `queued_duration` stays a fair picture of what is left to play.
CHUNK_S = 0.02

# Loud enough to be the agent talking, for turn-taking only.
#
# Fixed rather than adapted, because this decision is made while the recording
# is still being made. It sits well above comfort noise and well below speech,
# and being wrong by a little only makes the caller reply slightly early or
# slightly late -- which the recording captures faithfully either way.
LIVE_VOICE_FLOOR = 300.0

# How often the turn loop looks at the clock. Finer than this buys nothing: the
# quantity being measured is a settle time of about a second.
POLL_S = 0.05

# How long to wait for the worker to join before giving up on the call.
#
# The dispatch is asynchronous -- `create-web-call` returns as soon as the job
# is queued -- so some wait is always needed. A call where nobody ever joins is
# a broken deployment, not a prompt finding, and should say so rather than
# producing a recording of thirty seconds of nothing.
JOIN_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class SpokenLine:
    """A line the caller said, and exactly when it was heard.

    Not transcribed, because there is nothing to transcribe: the harness
    synthesized this audio and knows what is in it. The caller's half of the
    conversation is the one part of a voice test that is not a guess.
    """

    start: float
    end: float
    text: str


@dataclass
class CallerResult:
    agent_pcm: bytes
    agent_sample_rate: int
    caller_lines: list[SpokenLine]
    call_end: float
    stopped_because: str
    # Silence synthesized to fill gaps AFTER the agent's track was subscribed,
    # which is what "frames went missing" means. The wait for the track to
    # appear at the start of every call is not in here; see `_AgentTrack`.
    dropped_s: float = 0.0
    # Script lines the call ended before the caller could say. Carried as a
    # NUMBER rather than only inside a warning string because a reader
    # downstream has to act on it: anything the case expected from those lines
    # is unproven rather than failed, and only something machine-readable can
    # make the report say which.
    unspoken_lines: int = 0
    warnings: list[str] = field(default_factory=list)


class _AgentTrack:
    """The agent's audio, recorded and watched at the same time.

    Recording and turn-taking share one pass over the frames because they share
    the same frames; splitting them would mean two subscriptions to the same
    track and two slightly different ideas of when the agent was talking.
    """

    def __init__(self, started_at: float):
        self._started_at = started_at
        self.recording = Recording(sample_rate=SAMPLE_RATE)
        self.last_voice_at: float | None = None
        self.frames = 0
        # Silence padded in before the first frame ever arrived.
        #
        # Tracked apart from the rest because it is a different thing. The gap
        # between connecting and the agent's track being subscribed is
        # structural -- the track does not exist yet -- and it measures 1.4-1.8s
        # on a healthy local call. Padding AFTER that point means frames went
        # missing mid-call, which is a real problem. Reported as one number the
        # warning fires on every clean run, and a warning that is always on is
        # one nobody reads.
        self.lead_in_s = 0.0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at

    def feed(self, frame: rtc.AudioFrame) -> None:
        pcm = bytes(frame.data)
        at = self.elapsed
        self.recording.add(at - frame.duration, pcm)
        if self.frames == 0:
            self.lead_in_s = self.recording.padded_s
        self.frames += 1
        # Frame-level RMS, not windowed: LiveKit delivers 10ms frames and the
        # windows are 20ms, so a windowed measure sees nothing in any of them
        # and the caller waits out its timeout on every turn.
        if rms(pcm) >= LIVE_VOICE_FLOOR:
            self.last_voice_at = at


async def _drain(stream: rtc.AudioStream, track: _AgentTrack) -> None:
    async for event in stream:
        track.feed(event.frame)


def _reason_name(reason: object) -> str:
    """A `DisconnectReason` as something a person reading a report can use."""
    try:
        return rtc.DisconnectReason.Name(reason)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return repr(reason)


def _frames(pcm: bytes, *, rate: int = CALLER_RATE) -> list[rtc.AudioFrame]:
    """One synthesized line cut into frames LiveKit will accept."""
    return [
        rtc.AudioFrame(
            data=chunk,
            sample_rate=rate,
            num_channels=CALLER_CHANNELS,
            samples_per_channel=len(chunk) // 2,
        )
        for chunk in chunk_pcm(pcm, sample_rate=rate, chunk_s=CHUNK_S)
    ]


class Caller:
    """The harness's side of one call."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        settle_s: float,
        reply_timeout_s: float,
        max_call_s: float,
    ):
        self.url = url
        self.token = token
        self.settle_s = settle_s
        self.reply_timeout_s = reply_timeout_s
        self.max_call_s = max_call_s

        self.room = rtc.Room()
        self.started_at = 0.0
        self.agent: _AgentTrack | None = None
        self.agent_identity: str | None = None
        self.lines: list[SpokenLine] = []
        self.warnings: list[str] = []
        self.unspoken_lines = 0
        self._source: rtc.AudioSource | None = None
        self._readers: list[asyncio.Task] = []
        self._subscribed = asyncio.Event()
        # Set when there is nobody left to talk to. Without it the caller waits
        # out its full reply timeout for an answer that can never come, then
        # plays the rest of its script into an empty room -- and the recording
        # gains a stretch of silence no listener was ever in, which
        # `max_silence` then reports as dead air. Three of the first five Clara
        # cases failed that way, on calls where the agent behaved correctly.
        #
        # `_over_because` says WHICH, and the two do not mean the same thing:
        # `hung_up` is the agent ending the call, which is a fact about the
        # prompt; `room_closed` is the harness losing the room, which is a fact
        # about nothing. See `verdict.room_close_ended_the_call`.
        self._over = asyncio.Event()
        self._over_because = ""
        self._over_detail = ""

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def _end_call(self, why: str, detail: str = "") -> None:
        """Record the FIRST reason the call ended, and stop waiting.

        First rather than last: a hangup is followed by the room going away a
        moment later, and the second event must not overwrite the reason that
        explains the call.
        """
        if self._over.is_set():
            return
        self._over_because = why
        self._over_detail = detail
        self._over.set()

    async def connect(self) -> None:
        @self.room.on("track_subscribed")
        def _on_track(track: rtc.Track, _pub, participant: rtc.RemoteParticipant) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO or self.agent is not None:
                return
            log.info("subscribed to %s", participant.identity)
            self.agent = _AgentTrack(self.started_at)
            self.agent_identity = participant.identity
            stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
            self._readers.append(asyncio.create_task(_drain(stream, self.agent)))
            self._subscribed.set()

        @self.room.on("participant_disconnected")
        def _on_left(participant: rtc.RemoteParticipant) -> None:
            # Before the agent's track is subscribed there is nobody else it
            # could be, so an unknown identity counts too rather than being
            # ignored until the one case that matters.
            if self.agent_identity in (None, participant.identity):
                log.info("[%6.2fs] the agent hung up", self.elapsed)
                self._end_call("hung_up", "the agent left the room")

        @self.room.on("disconnected")
        def _on_room_closed(reason=None, *_args) -> None:
            # `reason` is a `DisconnectReason`, and reading it is the whole
            # point: the room going away because the worker deleted it is the
            # agent hanging up, and the room going away for any other reason is
            # this harness falling over. Defaulted so a signature change in
            # livekit-rtc degrades to the safe answer -- an unrecognised reason
            # is a broken run, not a clean one. Which is which lives in
            # `verdict.py`, where CI can reach it.
            named = _reason_name(reason)
            ended = room_close_ended_the_call(named)
            log.info("[%6.2fs] the room disconnected: %s", self.elapsed, named)
            self._end_call(
                "hung_up" if ended else "room_closed",
                f"the room ended by {named}" if ended else f"the room disconnected: {named}",
            )

        # The clock starts before connecting, not after the agent is heard. A
        # timeline that begins at the agent's first frame cannot represent the
        # agent being late, which is one of the things worth measuring.
        self.started_at = time.monotonic()
        await self.room.connect(self.url, self.token)

        self._source = rtc.AudioSource(CALLER_RATE, CALLER_CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track("caller", self._source)
        await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )

    async def wait_for_agent(self) -> bool:
        try:
            await asyncio.wait_for(self._subscribed.wait(), JOIN_TIMEOUT_S)
            return True
        except TimeoutError:
            self.warnings.append(
                f"no agent audio track after {JOIN_TIMEOUT_S:.0f}s — "
                "the worker never joined, so this run says nothing about the prompt"
            )
            return False

    async def say(self, text: str, pcm: bytes) -> SpokenLine:
        """Play one line and return when it has actually finished playing.

        `wait_for_playout` rather than a computed duration: the queue drains at
        the wire's pace, and a line that took longer to play than its samples
        say would otherwise have the caller's next turn start mid-sentence.
        """
        assert self._source is not None
        start = self.elapsed
        for frame in _frames(pcm):
            await self._source.capture_frame(frame)
        await self._source.wait_for_playout()
        line = SpokenLine(start=start, end=self.elapsed, text=text)
        self.lines.append(line)
        log.info("[%6.2fs] caller: %s", start, text)
        return line

    async def wait_for_turn(self) -> str:
        """Wait until the agent has spoken and then gone quiet.

        Returns why the wait ended, because the reasons mean different things:
        `settled` is an ordinary turn, `silent` is the agent never answering,
        `still_talking` is the agent running past the timeout, `hung_up` is the
        agent ending the call, and `room_closed` is this harness losing the
        room under itself. Only the first is the harness working as intended;
        the rest go in the report next to the recording that shows them, and
        the last two are graded differently -- one is about the prompt and the
        other is about nothing.

        `hung_up` and `room_closed` return immediately rather than waiting out
        the timeout. The difference is not just time: everything after the call
        ends is silence nobody heard, and leaving it in the recording turns a
        correct call into a dead-air finding.
        """
        assert self.agent is not None
        # The turn being waited for is anything the agent said since the caller
        # STARTED its own last line, not since this wait began. On a
        # speech-to-speech model the agent routinely answers over the caller's
        # tail; measured from now, that answer is in the past, the wait sees
        # nothing new, and a call the agent answered promptly is reported as
        # one where it never spoke.
        began = self.lines[-1].start if self.lines else 0.0
        deadline = self.elapsed + self.reply_timeout_s
        heard = False
        while self.elapsed < deadline:
            if self._over.is_set():
                return self._over_because
            voice = self.agent.last_voice_at
            if voice is not None and voice >= began:
                heard = True
                if self.elapsed - voice >= self.settle_s:
                    return "settled"
            await asyncio.sleep(POLL_S)
        return "still_talking" if heard else "silent"

    async def run(self, script: list[tuple[str, bytes]]) -> str:
        """The whole call: greet, reply, reply, and listen to the last word.

        Returns why it ended and does NOT clean up. Closing the room and
        assembling the recording belongs to `place_call`, which is the only
        place that sees every way this can end -- including the ways that
        raise.
        """
        await self.connect()
        stopped = "script_finished"
        if not await self.wait_for_agent():
            stopped = "agent_never_joined"
        else:
            for index, (text, pcm) in enumerate(script):
                if self.elapsed >= self.max_call_s:
                    stopped = "max_call_s"
                    self.unspoken_lines = len(script) - index
                    self.warnings.append(
                        f"stopped after {index} of {len(script)} lines: "
                        f"the call passed its {self.max_call_s:.0f}s limit"
                    )
                    break
                why = await self.wait_for_turn()
                if why in ("hung_up", "room_closed"):
                    stopped = "agent_ended_call" if why == "hung_up" else "room_closed"
                    self.unspoken_lines = len(script) - index
                    # Named rather than silently dropped, and NOT an error when
                    # it is a hangup: the agent deciding the call is over is a
                    # fact about the prompt, and often the very thing a case is
                    # grading. But the lines it cut off were never spoken, so
                    # anything the case expected from them is unproven rather
                    # than failed, and the report has to say which.
                    self.warnings.append(
                        f"{self._over_detail} with {self.unspoken_lines} of {len(script)} "
                        f"caller line(s) unspoken"
                    )
                    break
                if why != "settled":
                    self.warnings.append(
                        f"before line {index + 1} the agent was {why} for "
                        f"{self.reply_timeout_s:.0f}s"
                    )
                await self.say(text, pcm)
            else:
                # The closing matters as much as the opening -- a doubled
                # goodbye is the same bug as a doubled greeting -- so the last
                # thing the harness does is listen. Nothing is unspoken here:
                # the script finished, and only the trailing listen was cut.
                why = await self.wait_for_turn()
                if why == "hung_up":
                    stopped = "agent_ended_call"
                elif why == "room_closed":
                    stopped = "room_closed"
                    self.warnings.append(self._over_detail)

        return stopped

    async def finish(self, stopped: str) -> CallerResult:
        """Close everything down and assemble what was recorded.

        Safe to call after any failure, because that is the only reason it is
        separate from `run`: whatever went wrong, the audio captured up to that
        point is the most useful thing the run has, and losing it to a
        secondary error would be the worst outcome of all.
        """
        end = self.elapsed
        for reader in self._readers:
            reader.cancel()
        # `return_exceptions` rather than a bare await: a reader that already
        # died -- an AudioStream erroring on an abrupt disconnect, anything
        # raised inside `feed` -- is finished, so `cancel()` is a no-op and
        # awaiting it re-raises what killed it. That would propagate out of the
        # cleanup and discard the recording over a dropped stream.
        for outcome in await asyncio.gather(*self._readers, return_exceptions=True):
            if isinstance(outcome, Exception):
                self.warnings.append(f"the agent's audio stream failed: {outcome!r}")
        with contextlib.suppress(Exception):
            await self.room.disconnect()
        if self._source is not None:
            with contextlib.suppress(Exception):
                await self._source.aclose()

        recording = self.agent.recording if self.agent else Recording(sample_rate=SAMPLE_RATE)
        # Measured before the trailing pad, and net of the wait for the track
        # to appear: what is left is silence where frames should have been.
        dropped = max(0.0, recording.padded_s - (self.agent.lead_in_s if self.agent else 0.0))
        # Silence to the end of the call, so an agent that stopped answering
        # leaves a measurable gap rather than a short file.
        recording.pad_to(end)
        if self.agent is not None and self.agent.frames == 0:
            self.warnings.append("the agent's track carried no audio at all")

        return CallerResult(
            agent_pcm=recording.pcm,
            agent_sample_rate=SAMPLE_RATE,
            caller_lines=list(self.lines),
            call_end=end,
            stopped_because=stopped,
            dropped_s=dropped,
            unspoken_lines=self.unspoken_lines,
            warnings=list(self.warnings),
        )


async def place_call(
    *,
    url: str,
    token: str,
    script: list[tuple[str, bytes]],
    settle_s: float,
    reply_timeout_s: float,
    max_call_s: float,
) -> CallerResult:
    """One call, start to finish, with the room always closed behind it.

    "Always" is the point, and it has to cover every way this ends, not just
    the timeout: a bad token failing `room.connect`, a publish that is refused,
    an `rtc` error mid-call. Any of those escaping uncaught leaves a LiveKit
    room connected, an `AudioSource` open, reader tasks running, and -- worst
    of all -- throws away the audio recorded up to the failure, which is the
    most useful thing a broken run produces.
    """
    caller = Caller(
        url=url,
        token=token,
        settle_s=settle_s,
        reply_timeout_s=reply_timeout_s,
        max_call_s=max_call_s,
    )
    try:
        stopped = await asyncio.wait_for(
            caller.run(script), timeout=max_call_s + JOIN_TIMEOUT_S + 30
        )
    except TimeoutError:
        caller.warnings.append("the call was still running at the hard timeout")
        stopped = "hard_timeout"
    except asyncio.CancelledError:
        # Ctrl-C and shutdown are not findings. Close the room, then let the
        # cancellation continue on its way rather than swallowing it.
        await caller.finish("cancelled")
        raise
    except Exception as err:
        caller.warnings.append(f"the call ended in an error: {err!r}")
        log.exception("call failed")
        stopped = "error"
    return await caller.finish(stopped)
