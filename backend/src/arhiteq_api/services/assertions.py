"""Mechanical grading of a finished simulated call.

A judged criterion is a noisy sensor: two identical re-runs of the same suite
against the same prompt scored 69 and 62, with 21 of 98 cases flipping. That is
not a gate. These assertions are the other half of the answer — given a
transcript, every one of them decides the same way every time, because deciding
is ordinary code rather than a model's opinion.

Nothing here does I/O. It takes the transcript `_Simulator` built, the
assertions the case declared, and how the call ended, and returns one result per
assertion in the judge's own `{metric, passed, explanation}` shape — so the
dashboard renders an assertion exactly as it renders a graded criterion, with no
change to the frontend.

Patterns are plain Python regexes matched with `re.search`. Case-insensitivity
is written inline as `(?i)`; an exact match is anchored with `^` and `$`. The
design doc offered a second `/pattern/flags` syntax as well — one syntax is used
here on purpose, because two ways to write a pattern is a bug waiting for
whoever writes the hundredth case.

Two things to know before writing a case:

* `{"tool_called": "x", "times": 0}` is **not** how you say "never called". The
  "was never called" guard in `_check` fires before `times` is looked at, so
  that assertion can only ever fail. Spell it `{"tool_not_called": "x"}`. The
  guard order stays as it is on purpose: it is what makes every other
  `tool_called` failure explain *which* tools did run.
* Determinism is not something the assertions can supply on their own. A
  scripted case should carry a `tool_mock` for every tool it expects to be
  called, and should pin `current_time` in its `dynamic_variables`. An unmocked
  tool has its result invented by a model, and an unpinned clock reads
  `datetime.now(UTC)` — either one hands the same assertions a different call to
  grade on the next run, however mechanical the grading itself is.
"""

import json
import re
from typing import Any

# How `_Simulator` reports a caller who hung up (see `_user_turn`). `ends_with:
# caller_hangup` is the only assertion that reads the ending rather than the
# transcript, so this is the one string that has to stay in step with it.
CALLER_HUNG_UP = "the caller hung up"

# Tool *types* that take the call away from the agent — the same tuple
# `_Simulator` decides by (`simulation._TERMINAL_TOOL_TYPES`). This is the
# reliable signal: a tool of type `end_call` may be named anything the agent
# config likes, and the transcript records the type alongside the name.
_TERMINAL_TOOL_TYPES = ("end_call", "transfer_call")
# The same check for a transcript recorded before the type was written down.
# Matching by name misses a hang-up tool the config renamed, so it is the
# fallback rather than the rule.
_TERMINAL_TOOLS = ("end_call", "transfer_call")


def _invocations(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in transcript if item.get("role") == "tool_call_invocation"]


def _took_the_call_away(invocation: dict[str, Any]) -> bool:
    """Whether this call ended the conversation for the agent.

    `type` is what the simulator itself decides by, so it wins whenever the
    transcript carries it. Only runs recorded after the type was added have it;
    for anything older, fall back to the stock names — which is why a renamed
    hang-up tool in an old transcript is the one case this can still miss.
    """
    tool_type = invocation.get("type")
    if tool_type:
        return str(tool_type) in _TERMINAL_TOOL_TYPES
    return invocation.get("name") in _TERMINAL_TOOLS


def _spoken(transcript: list[dict[str, Any]]) -> list[str]:
    """What the caller actually heard.

    Tool traffic is deliberately excluded: a placeholder left literal inside a
    tool argument is a different bug from one read aloud, and `said_never` is
    about the second.
    """
    return [str(item.get("content") or "") for item in transcript if item.get("role") == "agent"]


def _arguments(invocation: dict[str, Any]) -> dict[str, Any]:
    """A call's arguments as a dict. Malformed JSON yields no arguments.

    The transcript stores them as a JSON *string* (`simulation.py` serializes
    them when recording the call), and a model that emitted something unparseable
    must fail an argument assertion rather than crash the whole run.
    """
    raw = invocation.get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except TypeError:
        return {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_text(value: Any) -> str:
    """An argument value as the text a pattern is matched against.

    Booleans and null render JSON-style rather than Python-style, so a case can
    write `"true"` and mean what the prompt and the wire mean by it.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class _BadPattern(Exception):
    """A case wrote a regex that does not compile."""


def _search(pattern: Any, text: str) -> bool:
    try:
        return re.search(str(pattern), text) is not None
    except re.error as exc:
        raise _BadPattern(f"invalid regex {pattern!r}: {exc}") from exc


def _matches_with(invocation: dict[str, Any], expected: dict[str, Any]) -> bool:
    """True when ONE call satisfies every listed argument pattern.

    Every pattern must hold on the same invocation. Spreading them across two
    calls would let an assertion about one action be satisfied by two unrelated
    ones — the SMS that went to the right number and the SMS that carried the
    right words being different messages, say.
    """
    args = _arguments(invocation)
    for name, pattern in expected.items():
        if name not in args:
            return False
        if not _search(pattern, _as_text(args[name])):
            return False
    return True


def _label(assertion: dict[str, Any]) -> str:
    """The one-line description shown wherever a graded criterion would be."""
    if not assertion:
        return "(empty assertion)"
    key = next(iter(assertion))
    value = assertion[key]
    extra = ""
    if key == "tool_called":
        if assertion.get("with"):
            extra += f" with {json.dumps(assertion['with'], ensure_ascii=False)}"
        if assertion.get("times") is not None:
            extra += f" exactly {assertion['times']}x"
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return f"{key}: {value}{extra}"


def _check(
    transcript: list[dict[str, Any]], assertion: dict[str, Any], ending: str
) -> tuple[bool, str]:
    """Decide one assertion. Returns (passed, explanation)."""
    if not assertion:
        return False, "The assertion is empty."
    key = next(iter(assertion))
    value = assertion[key]

    if key == "tool_called":
        matching = [c for c in _invocations(transcript) if c.get("name") == value]
        if not matching:
            called = sorted({str(c.get("name")) for c in _invocations(transcript)})
            return False, f"{value} was never called (called: {', '.join(called) or 'nothing'})."
        expected = assertion.get("with") or {}
        if expected:
            matching = [c for c in matching if _matches_with(c, expected)]
            if not matching:
                return False, (
                    f"{value} was called, but no single call matched "
                    f"{json.dumps(expected, ensure_ascii=False)}."
                )
        times = assertion.get("times")
        # Coerced, because a case that wrote "1" means one call. Left as a
        # string it would compare unequal to every count and fail with the
        # nonsense "matched 1 call(s), expected 1"; a value that is no kind of
        # number at all raises here and fails the assertion with a reason.
        if times is not None and len(matching) != int(times):
            return False, f"{value} matched {len(matching)} call(s), expected {times}."
        return True, f"{value} was called {len(matching)} time(s)."

    if key == "tool_not_called":
        if any(c.get("name") == value for c in _invocations(transcript)):
            return False, f"{value} was called."
        return True, f"{value} was never called."

    if key == "tool_order":
        wanted = [str(v) for v in (value or [])]
        remaining = list(wanted)
        for invocation in _invocations(transcript):
            if remaining and invocation.get("name") == remaining[0]:
                remaining.pop(0)
        if remaining:
            actual = [str(c.get("name")) for c in _invocations(transcript)]
            return False, (
                f"expected {' -> '.join(wanted)}; did not reach {remaining[0]} "
                f"(actual order: {' -> '.join(actual) or 'no calls'})."
            )
        return True, f"called in order: {' -> '.join(wanted)}."

    if key == "said_matches":
        for line in _spoken(transcript):
            if _search(value, line):
                return True, f"matched: {line[:120]!r}"
        return False, "No agent turn matched."

    if key == "said_never":
        for line in _spoken(transcript):
            if _search(value, line):
                return False, f"matched in: {line[:120]!r}"
        return True, "No agent turn matched."

    if key == "ends_with":
        invocations = _invocations(transcript)
        if value == "caller_hangup":
            if ending != CALLER_HUNG_UP:
                return False, f"the call ended because {ending}."
            hung_up = [c for c in invocations if _took_the_call_away(c)]
            if hung_up:
                return False, f"the agent called {hung_up[-1].get('name')} first."
            return True, "the caller hung up."
        if not invocations:
            return False, "no tool was called."
        last = str(invocations[-1].get("name"))
        if last != value:
            return False, f"the last tool called was {last}."
        return True, f"the last tool called was {value}."

    if key == "turn_count_max":
        turns = sum(1 for item in transcript if item.get("role") == "user")
        if turns > value:
            return False, f"the caller took {turns} turns, at most {value} allowed."
        return True, f"the caller took {turns} turn(s)."

    return False, (
        f"Unknown assertion type {key!r}. Known types: tool_called, "
        "tool_not_called, tool_order, said_matches, said_never, ends_with, "
        "turn_count_max."
    )


def evaluate(
    transcript: list[dict[str, Any]], assertions: list[Any], ending: str
) -> list[dict[str, Any]]:
    """Grade every assertion against a finished call.

    One result per assertion, in the order declared, in the judge's own shape so
    the dashboard needs no change. A malformed assertion fails its own assertion
    rather than the run: one bad entry must not take the batch down with it, and
    a case is hand-written JSON, so the ways it can be wrong are open-ended — a
    pattern that does not compile, a count written as a word, a `with` that is a
    string instead of a map. Anything raised while deciding one assertion is
    caught and reported against that assertion, and it *fails*: an assertion
    nobody could evaluate is never a pass.
    """
    results: list[dict[str, Any]] = []
    for assertion in assertions or []:
        if not isinstance(assertion, dict):
            results.append(
                {
                    "metric": str(assertion),
                    "passed": False,
                    "explanation": "An assertion must be an object.",
                }
            )
            continue
        try:
            passed, explanation = _check(transcript, assertion, ending)
        except _BadPattern as exc:
            passed, explanation = False, str(exc)
        except Exception as exc:  # noqa: BLE001 — a bad assertion fails, alone
            passed, explanation = (
                False,
                f"This assertion could not be evaluated: {type(exc).__name__}: {exc}",
            )
        results.append({"metric": _label(assertion), "passed": passed, "explanation": explanation})
    return results
