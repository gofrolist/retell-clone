"""Run one audio case against a live agent and report what it sounded like.

    python -m audio.run_case --case tests/audio/cases/greets-once.case.json \
        --agent-id agent_... --api-base http://127.0.0.1:8080

Everything expensive happens before the room is joined: the caller's lines are
synthesized (and cached) first, so the call itself contains no provider latency
that the timing rules would then blame on the agent.

Exit codes distinguish the two kinds of bad news, the same way layer 2 does:

    0  the call ran and sounded fine
    1  the call ran and the rules found something
    2  the run was broken — nobody joined, no audio, a provider refused

Only 1 is a statement about the prompt. A 2 is a statement about the harness or
the deployment, and treating it as a finding is how a suite loses its meaning.
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
from audio.caseload import Case, load_case
from audio.client import create_web_call, get_call, transcript_lines
from audio.pcm import slice_pcm, speech_spans, write_wav
from audio.voice import CALLER_SAMPLE_RATE, synthesize, transcribe

DEFAULT_CACHE = Path(__file__).parent / ".cache" / "caller-lines"
DEFAULT_ARTIFACTS = Path(__file__).parent / "artifacts"

log = logging.getLogger("audio-run")


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
    parser.add_argument("--keep", action="store_true", help="keep artifacts even when clean")
    return parser.parse_args(argv)


def resolve_agent_id(case: Case, args: argparse.Namespace) -> str:
    if args.agent_id:
        return args.agent_id
    if not args.agents:
        raise SystemExit(f"--agent-id or --agents needed to resolve '{case.agent}'")
    ids = json.loads(args.agents.read_text())
    agent_id = ids.get(case.agent)
    if not agent_id:
        raise SystemExit(f"{args.agents} has no '{case.agent}'")
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


def write_artifacts(directory: Path, *, result, segments, findings, call) -> None:
    """Everything needed to understand the call without placing another one."""
    directory.mkdir(parents=True, exist_ok=True)
    write_wav(result.agent_pcm, directory / "agent.wav", sample_rate=result.agent_sample_rate)
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
    if result.padded_s > 1.0:
        # Worth saying out loud: a recording that is mostly synthesized silence
        # is a network problem wearing a prompt finding's clothes.
        lines.append(f"  ! {result.padded_s:.1f}s of the recording is padding for missing frames")
    lines.append("")
    lines.append(format_findings(findings))
    lines.append(f"\nArtifacts: {directory}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    if not args.api_key:
        raise SystemExit("no workspace API key (--api-key or ARHITEQ_WORKSPACE_API_KEY)")
    if not args.cartesia_key:
        raise SystemExit("no Cartesia key (--cartesia-key or CARTESIA_API_KEY)")
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

    directory = args.artifacts / f"{case.name}-{call['call_id']}"
    write_artifacts(directory, result=result, segments=segments, findings=findings, call=record)
    print(report(case, result, segments, findings, directory))

    if result.stopped_because in {"agent_never_joined", "hard_timeout"} or not segments:
        return 2
    return 1 if findings else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(run(parse_args(argv if argv is not None else sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
