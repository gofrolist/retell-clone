"""What one case expects, checked against what was actually heard.

`analysis.py` holds the rules that apply to every call — nothing is said twice,
nobody is left in silence. This holds the ones that belong to a single case:
this call should have said the caller's name, should have logged the dose,
should never have offered to buy anything.

Pure and stdlib-only for the same reason as `analysis.py`: the worker's CI job
installs the dev group alone, so a judgement that imports nothing heavy is a
judgement CI can cover.

**Two sources, and the difference between them is the point of this layer.**
Speech expectations are checked against the ASR of the recording — what a
listener's ear would have received. Tool expectations are checked against the
platform's own `transcript_with_tool_calls`, because a tool call makes no sound
and there is nothing in the audio to check. A case that wants to know the agent
*said* something asks `heard`; a case that wants to know the agent *did*
something asks `tool_called`. The layers below grade both against one
transcript and cannot tell them apart.

**Patterns are matched against normalised text** (`analysis.normalise`:
lower-cased, apostrophes deleted, other punctuation blanked), case-insensitively.
That is not a convenience — it is what makes a pattern survive ASR, which
capitalises and punctuates by guesswork. It also means a pattern containing
punctuation matches nothing, which `caseload.parse_case` refuses at load time
rather than leaving to be discovered as a green run that graded nothing.

**Numbers are the sharp edge.** A transcriber may write "988" or "nine eight
eight" for the same audio, and which one it picks is not a fact about the
prompt. Write patterns that accept both — `(988|nine eight eight)` — rather
than betting on one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from audio.analysis import AGENT, Finding, Segment, normalise

# Every expectation a case may write. An unrecognised one is refused at load
# time by `caseload`, so nothing here has to defend against a typo.
KNOWN_EXPECTATIONS = ("heard", "never_heard", "tool_called", "tool_not_called")

# The roles the platform's transcript uses for a tool call and its result. The
# invocation is the only one read here: whether the agent DECIDED to call
# something is what a case grades, and a result the harness itself supplied
# grades the harness.
INVOCATION = "tool_call_invocation"


@dataclass(frozen=True)
class ToolCall:
    """One tool the agent invoked, as the platform recorded it."""

    name: str
    # Parsed from the record's JSON string. Empty when it would not parse,
    # never absent: a malformed argument blob is a call that happened, and a
    # `with` on it should fail on its own terms rather than crash the report.
    arguments: dict[str, Any]


def tool_calls(record: dict[str, Any]) -> list[ToolCall]:
    """The tool invocations in a platform call record, in order.

    Reads `transcript_with_tool_calls`, which is the only field carrying them —
    `transcript_object` drops tool calls, and the flat `transcript` string never
    had them. A record without the field yields nothing, which reads as "the
    agent called no tools" and fails a `tool_called` expectation. That is the
    right way round: the alternative is a case that silently passes because the
    evidence was missing rather than because the agent did the right thing.
    """
    calls = []
    for turn in record.get("transcript_with_tool_calls") or []:
        if not isinstance(turn, dict) or turn.get("role") != INVOCATION:
            continue
        name = str(turn.get("name") or "")
        if not name:
            continue
        calls.append(ToolCall(name=name, arguments=_arguments(turn.get("arguments"))))
    return calls


def _arguments(raw: object) -> dict[str, Any]:
    """A recorded `arguments` blob as a dict, or empty if it is not one.

    The worker serialises these to a JSON string (`state.py`), but a record
    read back from a database that was written by an older worker, or by a
    model that emitted something malformed, is not worth crashing a report
    over.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def matches(pattern: str, text: str) -> bool:
    """Whether a case's pattern is present in a piece of speech.

    `re.search` over normalised text, case-insensitively. Both halves matter:
    normalising the haystack is what makes ASR's punctuation irrelevant, and
    IGNORECASE is what stops a pattern written with a capital letter — which
    normalisation would never produce — from matching nothing at all.
    """
    return re.search(pattern, normalise(text), re.IGNORECASE) is not None


def _spoken(segments: list[Segment]) -> list[Segment]:
    return [s for s in segments if s.speaker == AGENT]


def check(
    expectations: list[dict[str, Any]],
    *,
    segments: list[Segment],
    calls: list[ToolCall],
    call_end: float,
) -> list[Finding]:
    """Every expectation of one case, as findings for the ones it missed.

    A satisfied expectation produces nothing. That is deliberate and it is the
    same contract `analysis.analyse` keeps: the report is a list of what is
    wrong, so a clean call prints nothing and a run that prints nothing is
    clean.

    Findings that are about the call as a whole — something never said, a tool
    never called — are placed at `call_end` so they sort after the moment-in-
    time findings in a report that reads as a walk through the recording. A
    `never_heard` violation has a real moment and is placed there instead.
    """
    findings: list[Finding] = []
    for expectation in expectations:
        findings.extend(_check_one(expectation, segments=segments, calls=calls, call_end=call_end))
    return sorted(findings, key=lambda f: f.at)


def _check_one(
    expectation: dict[str, Any],
    *,
    segments: list[Segment],
    calls: list[ToolCall],
    call_end: float,
) -> list[Finding]:
    if "heard" in expectation:
        return _heard(expectation["heard"], segments, call_end)
    if "never_heard" in expectation:
        return _never_heard(expectation["never_heard"], segments)
    if "tool_called" in expectation:
        return _tool_called(expectation, calls, call_end)
    if "tool_not_called" in expectation:
        return _tool_not_called(expectation["tool_not_called"], calls, call_end)
    # Unreachable through a loaded case — `caseload` refuses an unknown key —
    # but never silently pass one, because the whole value of an assertion is
    # that it cannot be satisfied by being ignored.
    return [
        Finding(
            rule="unknown_expectation",
            detail=f"nothing knows how to check {sorted(expectation)!r}",
            at=call_end,
        )
    ]


def _heard(pattern: str, segments: list[Segment], call_end: float) -> list[Finding]:
    if any(matches(pattern, s.text) for s in _spoken(segments)):
        return []
    return [
        Finding(
            rule="heard",
            detail=f"nothing the agent said matched {pattern!r}",
            at=call_end,
        )
    ]


def _never_heard(pattern: str, segments: list[Segment]) -> list[Finding]:
    return [
        Finding(
            rule="never_heard",
            detail=f"the agent said {segment.text!r}, which matches {pattern!r}",
            at=segment.start,
        )
        for segment in _spoken(segments)
        if matches(pattern, segment.text)
    ]


def _tool_called(
    expectation: dict[str, Any], calls: list[ToolCall], call_end: float
) -> list[Finding]:
    name = expectation["tool_called"]
    invoked = [call for call in calls if call.name == name]
    if not invoked:
        return [Finding(rule="tool_called", detail=f"{name} was never called", at=call_end)]

    # Exactly, not at least. Calling a tool twice is its own bug and a quiet
    # one: a Live-model call logged the same mood, the same dose and the same
    # outcome twice each, and every assertion that only asks whether the tool
    # ran read it as a clean call. Downstream it is a duplicated row in
    # somebody's record.
    times = expectation.get("times")
    if times is not None and len(invoked) != times:
        return [
            Finding(
                rule="tool_called",
                detail=f"{name} was called {len(invoked)}x, expected exactly {times}",
                at=call_end,
            )
        ]

    wanted = expectation.get("with")
    if not wanted:
        return []
    # Every listed argument has to match on ONE invocation, not across several.
    # Two calls each satisfying half of a `with` map is exactly the bug an
    # assertion about a single call exists to catch — a coupon texted to one
    # number and a message written for another.
    for call in invoked:
        if all(
            matches(pattern, str(call.arguments.get(key, ""))) for key, pattern in wanted.items()
        ):
            return []
    return [
        Finding(
            rule="tool_called",
            detail=(
                f"{name} was called {len(invoked)}x but no single call matched {wanted!r}; "
                f"saw {[call.arguments for call in invoked]!r}"
            ),
            at=call_end,
        )
    ]


def _tool_not_called(name: str, calls: list[ToolCall], call_end: float) -> list[Finding]:
    count = sum(1 for call in calls if call.name == name)
    if not count:
        return []
    return [
        Finding(
            rule="tool_not_called",
            detail=f"{name} was called {count}x and should not have been",
            at=call_end,
        )
    ]
