"""What an audio case is, and what makes one worthless.

An audio case costs a real call: a room, a live model, TTS both ways, and a
minute of wall clock. So the failure this file exists to prevent is not a crash
— it is a run that completes, reports nothing, and means nothing. A typo in
`variables` renders the prompt with its placeholder defaults; an empty script
turns the case into the agent talking to itself and reports a clean call.
Neither announces itself in the output.

Every field is therefore checked before the first request goes out, and an
unrecognised key is an error rather than a shrug — `varibles` silently dropping
sixteen dynamic variables is the exact shape of a bug that has already reached
production once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Sarah — the voice the platform launched with. The caller's voice does not
# have to match anything; it only has to stay the same, because it is part of
# the cache key for every synthesized line.
DEFAULT_CALLER_VOICE = "694f9389-aac1-45b6-b726-9d9369183238"

# Silence from the agent that means "your turn".
#
# Below the worker's own endpointing delays this would interrupt the agent
# mid-thought; far above them the caller sounds asleep and every turn adds dead
# air to the recording that the caller, not the agent, put there.
DEFAULT_SETTLE_S = 1.2

# How long a turn waits for the agent to say anything before giving up on it.
# Giving up is not a failure — the silence lands in the recording and
# `max_silence` reports it, which is the whole point.
DEFAULT_REPLY_TIMEOUT_S = 25.0

# A stop so a wedged call cannot hold a room and a Live session open forever.
DEFAULT_MAX_CALL_S = 180.0

KNOWN_KEYS = {
    "name",
    "agent",
    "agent_version",
    "voice",
    "variables",
    "script",
    "settle_s",
    "reply_timeout_s",
    "max_call_s",
    "notes",
}


class CaseError(ValueError):
    """A case that would run and tell you nothing."""


@dataclass(frozen=True)
class Case:
    name: str
    agent: str
    script: tuple[str, ...]
    variables: dict[str, str] = field(default_factory=dict)
    voice: str = DEFAULT_CALLER_VOICE
    agent_version: int | str | None = None
    settle_s: float = DEFAULT_SETTLE_S
    reply_timeout_s: float = DEFAULT_REPLY_TIMEOUT_S
    max_call_s: float = DEFAULT_MAX_CALL_S
    notes: str = ""


def _positive(raw: dict, key: str, fallback: float) -> float:
    if key not in raw:
        return fallback
    value = raw[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise CaseError(f"{key} must be a positive number, got {value!r}")
    return float(value)


def parse_case(raw: object, *, source: str = "case") -> Case:
    """A loaded JSON object as a `Case`, or an error naming what is wrong."""
    if not isinstance(raw, dict):
        raise CaseError(f"{source} must be a JSON object, got {type(raw).__name__}")

    unknown = sorted(set(raw) - KNOWN_KEYS)
    if unknown:
        raise CaseError(
            f"{source} has unrecognised key(s): {', '.join(unknown)}. "
            "A misspelled key is dropped in silence and the run still passes."
        )

    for required in ("name", "agent"):
        if not isinstance(raw.get(required), str) or not raw[required].strip():
            raise CaseError(f"{source} needs a non-empty '{required}'")

    script = raw.get("script")
    if not isinstance(script, list) or not script:
        raise CaseError(
            f"{source} needs a non-empty 'script'. With nothing to say, the caller "
            "is silent, the agent monologues, and the run reports a clean call."
        )
    for index, line in enumerate(script):
        if not isinstance(line, str) or not line.strip():
            raise CaseError(f"{source} script line {index + 1} is not a non-empty string")

    variables = raw.get("variables", {})
    if not isinstance(variables, dict):
        raise CaseError(f"{source} 'variables' must be an object")
    for key, value in variables.items():
        # The prompt interpolates these into text. A list or a dict renders as
        # its Python repr mid-sentence, which the model then reads aloud.
        if not isinstance(value, str):
            raise CaseError(
                f"{source} variable '{key}' must be a string, got {type(value).__name__}"
            )

    voice = raw.get("voice", DEFAULT_CALLER_VOICE)
    if not isinstance(voice, str) or not voice.strip():
        raise CaseError(f"{source} 'voice' must be a Cartesia voice id")

    return Case(
        name=raw["name"].strip(),
        agent=raw["agent"].strip(),
        script=tuple(line.strip() for line in script),
        variables=dict(variables),
        voice=voice,
        agent_version=raw.get("agent_version"),
        settle_s=_positive(raw, "settle_s", DEFAULT_SETTLE_S),
        reply_timeout_s=_positive(raw, "reply_timeout_s", DEFAULT_REPLY_TIMEOUT_S),
        max_call_s=_positive(raw, "max_call_s", DEFAULT_MAX_CALL_S),
        notes=str(raw.get("notes", "")),
    )


def load_case(path: Path | str) -> Case:
    file = Path(path)
    try:
        raw = json.loads(file.read_text())
    except FileNotFoundError as err:
        raise CaseError(f"{file} does not exist") from err
    except json.JSONDecodeError as err:
        raise CaseError(f"{file} is not valid JSON: {err}") from err
    return parse_case(raw, source=str(file))


def discover(directory: Path | str) -> list[Case]:
    """Every `*.case.json` under a directory, in filename order.

    Sorted so a suite runs in the same order everywhere, and so a report can be
    diffed against the last one.
    """
    root = Path(directory)
    return [load_case(path) for path in sorted(root.glob("**/*.case.json"))]
