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

The same standard is what `expect` is held to. An expectation that cannot fail
is not a weaker version of one that can: it is a case that reports coverage of
something it never checked, and it survives exactly as long as nobody looks. So
a pattern is compiled here, and refused here if ASR could never produce the
text it looks for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from audio.expectations import KNOWN_EXPECTATIONS

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

# The version references the control plane understands, beside a version NUMBER
# (`services/versions.py`). They are not interchangeable and a case should say
# which it means: `latest` is the draft the editor shows, `latest_published` is
# what a live call runs.
VERSION_REFS = ("latest", "latest_published")

KNOWN_KEYS = {
    "name",
    "agent",
    "agent_version",
    "voice",
    "variables",
    "variables_from",
    "script",
    "expect",
    "tools",
    "settle_s",
    "reply_timeout_s",
    "max_call_s",
    "notes",
}

# Characters that mean a pattern was written for a transcript rather than for
# speech, and would therefore match nothing however the call went.
#
# `expectations.matches` searches NORMALISED text — apostrophes deleted, every
# other punctuation mark blanked — so a pattern carrying punctuation is a
# green run that graded nothing. Only marks that cannot be regex syntax are
# listed: a comma is a quantifier bound, a colon is `(?:`, a brace is `{2}`,
# and refusing those would refuse valid patterns.
UNMATCHABLE_CHARS = "'’\"!;"

# The one that has to be called out by name. Layer 1 asserts `said_never:
# '\{\{'` to catch an unrendered placeholder reaching the listener, and
# translating that pattern to this layer produces an assertion that can never
# fire: ASR of a model reading `{{first_name}}` aloud returns the words "first
# name", never the braces. The audio spelling of the same check is
# `{"never_heard": "first name"}`.
BRACES = ("{{", "}}")


class CaseError(ValueError):
    """A case that would run and tell you nothing."""


@dataclass(frozen=True)
class Case:
    name: str
    agent: str
    script: tuple[str, ...]
    variables: dict[str, str] = field(default_factory=dict)
    # What this call in particular should have sounded like and done. The
    # global rules in `analysis.py` run on every case regardless; these are the
    # ones only this case can state. Empty is allowed — a smoke case that only
    # proves the plumbing has nothing of its own to say.
    expect: tuple[dict, ...] = ()
    # Tool name → the JSON body the local sink answers with, mirroring layer
    # 1's `tools:`. What makes this SAFETY rather than fidelity: Clara's tools
    # are real Supabase functions, so an unsunk call logs a real dose and texts
    # a real family member. See `toolsink.py`.
    tools: dict[str, str] = field(default_factory=dict)
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


def _agent_version(raw: dict, *, source: str) -> int | str | None:
    """Which version of the agent to dial, checked here rather than by the API.

    Unchecked, this is the one field in a case that reaches the wire as written:
    it becomes a `?version=` query parameter, and `lates_published` or `2.5`
    comes back as a 422 that surfaces as a broken run with the control plane's
    wording, several seconds and one synthesis pass later. A case that names a
    field wrong should be told so by the loader, with the field's name in the
    message, before anything is spent on it.
    """
    if "agent_version" not in raw:
        return None
    value = raw["agent_version"]
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise CaseError(f"{source} 'agent_version' must be 1 or more, got {value}")
        return value
    if isinstance(value, str) and value.strip() in VERSION_REFS:
        return value.strip()
    raise CaseError(
        f"{source} 'agent_version' must be a version number or one of "
        f"{', '.join(VERSION_REFS)}, got {value!r}"
    )


def _pattern(value: object, *, source: str, where: str) -> str:
    """One regex from a case, checked for the ways it could never match.

    A pattern that cannot fire is the worst thing in a suite: the case runs,
    costs a call, reports clean, and is counted as coverage of the thing it
    never checked. All three refusals below have a green run as their symptom.
    """
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{source} {where} must be a non-empty pattern")
    # Escapes are dropped before looking, because the pattern this refusal
    # exists for is the escaped one: layer 1 writes `\{\{`, and searching for
    # the bare `{{` would miss the exact string a person translating a case by
    # hand would paste in.
    unescaped = value.replace("\\", "")
    for brace in BRACES:
        if brace in unescaped:
            raise CaseError(
                f"{source} {where} looks for {brace!r}, which no transcript of speech "
                "contains: a model reading '{{first_name}}' aloud is heard as the words "
                "'first name'. Match those instead."
            )
    bad = sorted({c for c in value if c in UNMATCHABLE_CHARS})
    if bad:
        raise CaseError(
            f"{source} {where} contains {''.join(bad)!r}, which is removed before matching "
            "— write the pattern without punctuation"
        )
    try:
        re.compile(value)
    except re.error as err:
        raise CaseError(f"{source} {where} is not a valid regex: {err}") from err
    return value


def _expectation(raw: object, *, source: str, index: int) -> dict:
    """One entry of a case's `expect` list.

    The shape is layer 1's `assert` shape — a dict whose single expectation key
    names the check — so the same case reads the same way in both places and
    one can be translated to the other by hand without learning a second
    syntax.
    """
    where = f"expect[{index}]"
    if not isinstance(raw, dict):
        raise CaseError(f"{source} {where} must be an object, got {type(raw).__name__}")

    named = [key for key in raw if key in KNOWN_EXPECTATIONS]
    if len(named) != 1:
        raise CaseError(
            f"{source} {where} must name exactly one of {', '.join(KNOWN_EXPECTATIONS)}, "
            f"got {sorted(raw) or 'nothing'}"
        )
    key = named[0]
    extra = sorted(set(raw) - {key, "with", "times"})
    if extra:
        raise CaseError(f"{source} {where} has unrecognised key(s): {', '.join(extra)}")

    if key in ("heard", "never_heard"):
        for qualifier in ("with", "times"):
            if qualifier in raw:
                raise CaseError(
                    f"{source} {where}: {qualifier!r} belongs to tool_called, not {key}"
                )
        return {key: _pattern(raw[key], source=source, where=f"{where}.{key}")}

    name = raw[key]
    if not isinstance(name, str) or not name.strip():
        raise CaseError(f"{source} {where}.{key} must be a tool name")
    if key == "tool_not_called" and ("with" in raw or "times" in raw):
        # A qualifier here reads as "not called with these arguments" or "not
        # called this many times" and means nothing of the sort — it would be
        # ignored, and the case would assert something weaker than its author
        # wrote.
        raise CaseError(
            f"{source} {where}: tool_not_called takes no 'with' or 'times'. To allow a "
            "tool but forbid one shape of it, assert tool_called with the shape you want."
        )
    expectation: dict = {key: name.strip()}

    times = raw.get("times")
    if times is not None:
        # Bools are ints in Python and `times: true` would sail through as one.
        if not isinstance(times, int) or isinstance(times, bool) or times < 1:
            raise CaseError(f"{source} {where}.times must be a positive whole number")
        expectation["times"] = times

    wanted = raw.get("with")
    if wanted is not None:
        if not isinstance(wanted, dict) or not wanted:
            raise CaseError(f"{source} {where}.with must be a non-empty object")
        expectation["with"] = {
            str(arg): _pattern(pattern, source=source, where=f"{where}.with.{arg}")
            for arg, pattern in wanted.items()
        }
    return expectation


def _tools(raw: object, *, source: str) -> dict[str, str]:
    """The canned tool results, checked for the ones that would derail a call.

    A result that is not JSON reaches the model as a broken payload and steers
    every turn after it, which grades the harness rather than the prompt.
    """
    if not isinstance(raw, dict):
        raise CaseError(f"{source} 'tools' must be an object of tool name → JSON result")
    checked = {}
    for name, result in raw.items():
        if not isinstance(result, str):
            raise CaseError(
                f"{source} tools.{name} must be a JSON STRING, the way the tool would "
                f"return one, got {type(result).__name__}"
            )
        try:
            json.loads(result)
        except json.JSONDecodeError as err:
            raise CaseError(f"{source} tools.{name} is not valid JSON: {err}") from err
        checked[name] = result
    return checked


def _variables(raw: dict, *, source: str, base_dir: Path | None) -> dict[str, str]:
    """A case's variables, with any it inherits underneath its own.

    `variables_from` exists because the check-in prompt reads sixteen dynamic
    variables and an omitted one does not fail — it reaches the model as
    literal braces and can be read to the listener. Repeating sixteen lines in
    every case buries the one or two a case is actually about, and copies are
    what drift. So the shared set is a file, and a case names it; what stays in
    the case file is the override, which is the sentence the case is making.
    """
    own = raw.get("variables", {})
    if not isinstance(own, dict):
        raise CaseError(f"{source} 'variables' must be an object")

    inherited: dict = {}
    shared = raw.get("variables_from")
    if shared is not None:
        if not isinstance(shared, str) or not shared.strip():
            raise CaseError(f"{source} 'variables_from' must be a filename")
        if base_dir is None:
            raise CaseError(
                f"{source} uses 'variables_from', which only a case loaded from a file can resolve"
            )
        path = base_dir / shared
        try:
            inherited = json.loads(path.read_text())
        except FileNotFoundError as err:
            raise CaseError(f"{source} inherits from {path}, which does not exist") from err
        except json.JSONDecodeError as err:
            raise CaseError(f"{path} is not valid JSON: {err}") from err
        if not isinstance(inherited, dict):
            raise CaseError(f"{path} must be an object of variable name → value")
        # `$`-prefixed keys are the shared file's own notes, the spelling
        # `manifest.json` and the prompt repo already use. A shared file is
        # where the reasoning behind a value belongs, and JSON has nowhere else
        # to put it.
        inherited = {k: v for k, v in inherited.items() if not k.startswith("$")}

    merged = {**inherited, **own}
    for key, value in merged.items():
        # The prompt interpolates these into text. A list or a dict renders as
        # its Python repr mid-sentence, which the model then reads aloud.
        if not isinstance(value, str):
            raise CaseError(
                f"{source} variable '{key}' must be a string, got {type(value).__name__}"
            )
    return merged


def parse_case(raw: object, *, source: str = "case", base_dir: Path | None = None) -> Case:
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

    variables = _variables(raw, source=source, base_dir=base_dir)

    expect = raw.get("expect", [])
    if not isinstance(expect, list):
        raise CaseError(f"{source} 'expect' must be a list of expectations")
    expectations = tuple(
        _expectation(item, source=source, index=index) for index, item in enumerate(expect)
    )

    voice = raw.get("voice", DEFAULT_CALLER_VOICE)
    if not isinstance(voice, str) or not voice.strip():
        raise CaseError(f"{source} 'voice' must be a Cartesia voice id")

    return Case(
        name=raw["name"].strip(),
        agent=raw["agent"].strip(),
        script=tuple(line.strip() for line in script),
        variables=dict(variables),
        expect=expectations,
        tools=_tools(raw.get("tools", {}), source=source),
        voice=voice,
        agent_version=_agent_version(raw, source=source),
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
    return parse_case(raw, source=str(file), base_dir=file.parent)


def discover(directory: Path | str) -> list[Case]:
    """Every `*.case.json` under a directory, in filename order.

    Sorted so a suite runs in the same order everywhere, and so a report can be
    diffed against the last one.
    """
    root = Path(directory)
    return [load_case(path) for path in sorted(root.glob("**/*.case.json"))]
