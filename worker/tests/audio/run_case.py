"""Run one audio case against a live agent and report what it sounded like.

    python -m audio.run_case --case tests/audio/cases/greets-once.case.json \
        --agent-id agent_... --api-base http://127.0.0.1:8080

Everything expensive happens before the room is joined: the caller's lines are
synthesized (and cached) first, so the call itself contains no provider latency
that the timing rules would then blame on the agent.

Exit codes distinguish the two kinds of bad news, the same way layer 2 does;
the rule itself lives in `verdict.py`, where it can be tested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

from audio.analysis import AGENT, CALLER, Segment, analyse, format_findings
from audio.caller import place_call
from audio.caseload import Case, CaseError, load_case
from audio.client import ControlPlaneError, create_web_call, get_call, transcript_lines
from audio.pcm import slice_pcm, speech_spans, write_wav
from audio.verdict import BROKEN, describe, exit_code
from audio.voice import CALLER_SAMPLE_RATE, VoiceError, synthesize, transcribe

DEFAULT_CACHE = Path(__file__).parent / ".cache" / "caller-lines"
DEFAULT_ARTIFACTS = Path(__file__).parent / "artifacts"

# Padding past the wait for the agent's track that counts as frames going
# missing. The wait itself is 1.4-1.8s on a healthy local call and is measured
# separately, so this is only about gaps mid-recording.
DROPPED_AUDIO_WARNING_S = 0.5

log = logging.getLogger("audio-run")


class HarnessError(RuntimeError):
    """The run could not be set up. Never a statement about the prompt."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="audio.run_case", description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--agent-id", help="agent to call; overrides --agents")
    parser.add_argument("--agents", type=Path, help="JSON map of agent name → agent id")
    parser.add_argument("--api-base", default=os.getenv("ARHITEQ_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--api-key", default=os.getenv("ARHITEQ_WORKSPACE_API_KEY", ""))
    parser.add_argument("--cartesia-key", default=os.getenv("CARTESIA_API_KEY", ""))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    return parser.parse_args(argv)


def resolve_agent_id(case: Case, args: argparse.Namespace) -> str:
    if args.agent_id:
        return args.agent_id
    if not args.agents:
        raise HarnessError(f"--agent-id or --agents needed to resolve '{case.agent}'")
    ids = json.loads(args.agents.read_text())
    agent_id = ids.get(case.agent)
    if not agent_id:
        raise HarnessError(f"{args.agents} has no '{case.agent}'")
    return agent_id


def render_script(case: Case, *, api_key: str, cache: Path) -> list[tuple[str, bytes]]:
    """Every caller line, synthesized before the call rather than during it.

    Rendering mid-call would put a synthesis round trip inside a turn, and the
    silence it produces lands in the recording as the agent's dead air.
    """
    with httpx.Client(timeout=60.0) as http:
        return [
            (
                line,
                synthesize(
                    line,
                    voice=case.voice,
                    api_key=api_key,
                    sample_rate=CALLER_SAMPLE_RATE,
                    cache_dir=cache,
                    client=http,
                ),
            )
            for line in case.script
        ]


def agent_segments(pcm: bytes, *, sample_rate: int, api_key: str) -> list[Segment]:
    """The agent's speech, found in the recording and then read back.

    Segmented first and transcribed second, so the boundaries come from the
    audio rather than from whatever the transcriber decided to split on. A
    transcriber's idea of a sentence is not the same as a listener's idea of a
    turn, and the timing rules are about the second one.
    """
    segments = []
    with httpx.Client(timeout=60.0) as http:
        for start, end in speech_spans(pcm, sample_rate=sample_rate):
            span = slice_pcm(pcm, start, end, sample_rate=sample_rate)
            text = transcribe(span, api_key=api_key, sample_rate=sample_rate, client=http)
            segments.append(Segment(start=start, end=end, text=text, speaker=AGENT))
    return segments


def write_recording(directory: Path, result) -> None:
    """The audio, on disk, before anything else is attempted.

    Written the moment the call ends rather than at the end of the run. What
    follows it -- one transcription round trip per span, then fetching the
    platform's record -- is all network, and any of it can fail: a 429 from the
    speech provider, a 404 while the backend is still finalizing the call. If
    the WAV is written last, that failure throws away a recording that cost a
    minute of wall clock and real provider spend, and the promise this whole
    layer makes -- that a failure hands you the two seconds that broke instead
    of asking you to place another call -- is broken precisely when it is
    needed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    write_wav(result.agent_pcm, directory / "agent.wav", sample_rate=result.agent_sample_rate)


def write_report(directory: Path, *, segments, findings, call) -> None:
    """Everything else needed to understand the call without placing another."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "segments.json").write_text(
        json.dumps(
            [
                {"start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text}
                for s in segments
            ],
            indent=2,
        )
        + "\n"
    )
    (directory / "findings.txt").write_text(format_findings(findings) + "\n")
    (directory / "call.json").write_text(json.dumps(call, indent=2, default=str) + "\n")


def report(case: Case, result, segments, findings, directory: Path) -> str:
    lines = [f"\n{case.name}  ({result.call_end:.1f}s, stopped: {result.stopped_because})"]
    for segment in segments:
        who = "agent " if segment.speaker == AGENT else "caller"
        lines.append(f"  [{segment.start:6.2f}s] {who}: {segment.text}")
    for warning in result.warnings:
        lines.append(f"  ! {warning}")
    if result.dropped_s > DROPPED_AUDIO_WARNING_S:
        # Worth saying out loud: a recording that is partly synthesized silence
        # is a network problem wearing a prompt finding's clothes. Measured net
        # of the wait for the agent's track, which is 1.4-1.8s on a healthy
        # call — counted in, this warning fires on every clean run, and one
        # that is always on is one nobody reads.
        lines.append(f"  ! {result.dropped_s:.1f}s of audio went missing mid-call")
    lines.append("")
    lines.append(format_findings(findings))
    lines.append(f"\nArtifacts: {directory}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    if not args.api_key:
        raise HarnessError("no workspace API key (--api-key or ARHITEQ_WORKSPACE_API_KEY)")
    if not args.cartesia_key:
        raise HarnessError("no Cartesia key (--cartesia-key or CARTESIA_API_KEY)")
    agent_id = resolve_agent_id(case, args)

    script = render_script(case, api_key=args.cartesia_key, cache=args.cache)
    call = create_web_call(
        args.api_base,
        args.api_key,
        agent_id,
        variables=case.variables,
        agent_version=case.agent_version,
        metadata={"harness": "audio", "case": case.name},
    )
    log.info("call %s in room via %s", call["call_id"], call["livekit_server_url"])

    result = await place_call(
        url=call["livekit_server_url"],
        token=call["access_token"],
        script=script,
        settle_s=case.settle_s,
        reply_timeout_s=case.reply_timeout_s,
        max_call_s=case.max_call_s,
    )

    directory = args.artifacts / f"{case.name}-{call['call_id']}"
    write_recording(directory, result)

    segments = agent_segments(
        result.agent_pcm, sample_rate=result.agent_sample_rate, api_key=args.cartesia_key
    )
    segments += [
        Segment(start=line.start, end=line.end, text=line.text, speaker=CALLER)
        for line in result.caller_lines
    ]
    segments.sort(key=lambda s: s.start)
    findings = analyse(segments, call_end=result.call_end)

    # Fetched after the call so the platform has written its transcript. Kept
    # beside the recording because the disagreement between the two — heard
    # twice, logged once — is itself a finding no single source can show.
    record = get_call(args.api_base, args.api_key, call["call_id"])
    record["transcript_lines"] = transcript_lines(record)

    write_report(directory, segments=segments, findings=findings, call=record)
    print(report(case, result, segments, findings, directory))

    return exit_code(stopped=result.stopped_because, segments=len(segments), findings=len(findings))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        return asyncio.run(run(parse_args(argv if argv is not None else sys.argv[1:])))
    except (HarnessError, CaseError, ControlPlaneError, VoiceError, OSError) as err:
        # A misconfigured harness is not a prompt regression. Left to the
        # default traceback these all exit 1, which a nightly reads as "the
        # rules found something" — so rotating a credential would be reported
        # as the prompt getting worse.
        print(f"{describe(BROKEN)}: {err}", file=sys.stderr)
        return BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
