# Scripted Caller & Deterministic Assertions Implementation Plan (strategy steps 4–5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a simulation case able to walk the same conversation every run and be graded by mechanical assertions instead of an LLM judge, so a prompt change gets a red-or-green answer that does not move when nothing changed.

**Architecture:** Two additive nullable columns on `TestCaseDefinition` carry a caller `script` and a list of `assertions`. When a case has a script, `_Simulator._user_turn` pops literal lines instead of calling the user-simulator model. When a case has assertions, a new pure module `services/assertions.py` evaluates them over the finished transcript — no LLM, no I/O — and emits results in the existing `{metric, passed, explanation}` shape, so the dashboard renders them with no frontend change. A case carrying only assertions never invokes the judge at all.

**Tech Stack:** Python 3.14, uv, FastAPI, SQLAlchemy async, pytest. Backend only.

## Global Constraints

- **The wire contract is frozen** (`CLAUDE.md`, `docs/RETELL_INTEGRATION_MAP.md`). Extra fields are fine; renames and drops are not. Every field this plan adds is new and optional — nothing existing changes name, type or nesting.
- **Existing cases must keep working untouched.** `script` and `assertions` are nullable. A case with neither behaves exactly as it does today: improvising caller, judge grades the metrics.
- **No Alembic.** Schema changes are `create_all` for new tables plus an entry in `_COLUMN_BACKFILLS` in `backend/src/arhiteq_api/main.py:60-86` for new columns. Every backfill must be idempotent.
- **`services/assertions.py` is pure**: no database, no network, no LLM, no `process.env`. It takes a transcript and a list of assertions and returns results.
- **Result shape is fixed**: `{"metric": str, "passed": bool, "explanation": str}` — identical to the judge's, because `frontend/src/components/simulation/RunDrawer.tsx:68-85` renders that shape generically and must not need changing.
- **An unrecognized assertion fails.** Never silently pass. Same rule the judge path already applies to an ungraded metric (`simulation.py:939-949`).
- Backend tests: `cd backend && uv run pytest`
- `main` is protected: this lands as one squash-merged PR with a conventional-commit title.

## Phase structure

**Phase A (Tasks 1–3) is a `retell-clone` PR.** It ships the engine capability. It is complete and useful on its own: the dashboard can author a scripted case with assertions the moment it merges.

**Phase B (sketched at the end) is a `usan-retirement-backend` PR** that writes the ~15 Clara cases and turns on the prompt-repo gate. **It cannot start until Phase A is merged and released**, because the cases are authored against the shipped case format. Phase B is deliberately left as an outline here and gets its own plan once Phase A is released — the last plan in this series specified code before the thing existed and several of its blocks turned out to be wrong.

---

### Task 1: `services/assertions.py` — the pure evaluator

The whole determinism argument rests on this file, and it is ordinary code testable by ordinary means. Build it first and completely, before anything touches the database or the simulator.

**Files:**
- Create: `backend/src/arhiteq_api/services/assertions.py`
- Create: `backend/tests/unit/test_assertions.py`

**Interfaces:**
- Consumes: nothing. Pure functions over plain data.
- Produces: `evaluate(transcript: list[dict], assertions: list[dict], ending: str) -> list[dict]` — one result dict per assertion, in order, each `{"metric": str, "passed": bool, "explanation": str}`.

**The transcript shape** is what `_Simulator` builds (`services/simulation.py:736-784`). Four roles:

```python
{"role": "agent", "content": "..."}
{"role": "user", "content": "..."}
{"role": "tool_call_invocation", "name": "log_mood", "arguments": '{"phone": "+1..."}', "tool_call_id": "tool_ab12"}
{"role": "tool_call_result", "name": "log_mood", "content": '{"ok": true}', "tool_call_id": "tool_ab12"}
```

`arguments` is a **JSON string**, not a dict — it is serialized at `simulation.py:761`. The evaluator must parse it, and must not raise if it is malformed.

**`ending`** is `_Simulator.ending`, one of these exact strings (`simulation.py:586, 860-877`):
`"the caller hung up"`, `"the agent ended it"`, `"the harness hit its turn limit before the call ended"`, `"the harness got no reply from the simulated caller"`, `"the call did not get started"`.

**The seven assertion types.** Each assertion is a dict whose single type key names it:

| Assertion | Shape | Passes when |
|---|---|---|
| `tool_called` | `{"tool_called": "log_mood", "with": {"phone": "^\\+1"}, "times": 1}` | The tool was invoked; if `with`, at least one invocation has every listed argument matching; if `times`, the invocation count equals it exactly |
| `tool_not_called` | `{"tool_not_called": "purchase_offer"}` | No invocation of that tool |
| `tool_order` | `{"tool_order": ["to_pharmacy", "hand_back_to_checkin"]}` | Those names appear in the invocation log in that relative order (a subsequence — other calls may sit between them) |
| `said_matches` | `{"said_matches": "(?i)good morning"}` | Some agent turn matches the pattern |
| `said_never` | `{"said_never": "\\{\\{"}` | No agent turn matches the pattern |
| `ends_with` | `{"ends_with": "end_call"}` or `{"ends_with": "caller_hangup"}` | For a tool name: the last tool invocation in the transcript has that name. For `caller_hangup`: `ending == "the caller hung up"` **and** no `end_call`/`transfer_call` invocation exists |
| `turn_count_max` | `{"turn_count_max": 12}` | The number of `user` turns is ≤ N |

**Pattern syntax, deviating from the spec deliberately.** The design doc wrote patterns two ways — bare regex (`said_never: '\{\{'`) and slash-delimited with flags (`with: {name: /lipitor/i}`). Two syntaxes in one file is a bug waiting to happen. **Every pattern here is a plain Python regex string, matched with `re.search`.** Case-insensitivity is written inline as `(?i)`. Anchor with `^`/`$` for an exact match. Document this at the top of the module.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_assertions.py`:

```python
"""The mechanical half of grading a simulated call.

Every test builds a transcript literally — no database, no LLM, no fixtures —
because that is the property the whole determinism argument rests on.
"""

from arhiteq_api.services.assertions import evaluate

HUNG_UP = "the caller hung up"


def agent(text):
    return {"role": "agent", "content": text}


def user(text):
    return {"role": "user", "content": text}


def call(name, arguments=None):
    return {
        "role": "tool_call_invocation",
        "name": name,
        "arguments": arguments if arguments is not None else "{}",
        "tool_call_id": f"tool_{name}",
    }


def result(name, content='{"ok": true}'):
    return {
        "role": "tool_call_result",
        "name": name,
        "content": content,
        "tool_call_id": f"tool_{name}",
    }


def only(transcript, assertion, ending=HUNG_UP):
    """Evaluate one assertion and return its single result."""
    results = evaluate(transcript, [assertion], ending)
    assert len(results) == 1
    return results[0]


# --- tool_called ---------------------------------------------------------


def test_tool_called_passes_when_invoked():
    t = [agent("hi"), call("log_mood"), result("log_mood")]
    assert only(t, {"tool_called": "log_mood"})["passed"] is True


def test_tool_called_fails_when_never_invoked():
    r = only([agent("hi")], {"tool_called": "log_mood"})
    assert r["passed"] is False
    assert "log_mood" in r["explanation"]


def test_tool_called_with_matching_argument_passes():
    t = [call("log_medication_taken", '{"medication_name": "Lipitor 20mg"}')]
    assert only(t, {"tool_called": "log_medication_taken", "with": {"medication_name": "(?i)lipitor"}})["passed"] is True


def test_tool_called_with_non_matching_argument_fails():
    t = [call("log_medication_taken", '{"medication_name": "Metformin"}')]
    r = only(t, {"tool_called": "log_medication_taken", "with": {"medication_name": "(?i)lipitor"}})
    assert r["passed"] is False


def test_tool_called_with_matches_across_separate_invocations_only_if_one_call_has_all():
    # Two calls, each matching half the `with` map. Neither satisfies both, so
    # this must fail — otherwise an assertion about one call could be satisfied
    # by two unrelated ones.
    t = [
        call("send_family_sms", '{"phone": "+15551234567", "message": "hello"}'),
        call("send_family_sms", '{"phone": "+19998887777", "message": "checking in"}'),
    ]
    r = only(t, {"tool_called": "send_family_sms", "with": {"phone": "\\+1555", "message": "checking"}})
    assert r["passed"] is False


def test_tool_called_times_counts_exactly():
    t = [call("log_mood"), call("log_mood")]
    assert only(t, {"tool_called": "log_mood", "times": 2})["passed"] is True
    assert only(t, {"tool_called": "log_mood", "times": 1})["passed"] is False


def test_tool_called_tolerates_malformed_arguments():
    t = [call("log_mood", "not json at all")]
    # The call happened, so the bare assertion passes; a `with` on it cannot.
    assert only(t, {"tool_called": "log_mood"})["passed"] is True
    assert only(t, {"tool_called": "log_mood", "with": {"phone": "."}})["passed"] is False


def test_tool_called_matches_non_string_arguments_by_their_text():
    t = [call("log_mood", '{"mood_score": 4, "slept_well": true}')]
    assert only(t, {"tool_called": "log_mood", "with": {"mood_score": "^4$"}})["passed"] is True
    assert only(t, {"tool_called": "log_mood", "with": {"slept_well": "true"}})["passed"] is True


# --- tool_not_called -----------------------------------------------------


def test_tool_not_called_passes_when_absent():
    assert only([agent("hi")], {"tool_not_called": "purchase_offer"})["passed"] is True


def test_tool_not_called_fails_when_present():
    t = [call("purchase_offer")]
    r = only(t, {"tool_not_called": "purchase_offer"})
    assert r["passed"] is False
    assert "purchase_offer" in r["explanation"]


# --- tool_order ----------------------------------------------------------


def test_tool_order_passes_as_a_subsequence():
    t = [call("to_pharmacy"), call("medication_price_lookup"), call("hand_back_to_checkin")]
    assert only(t, {"tool_order": ["to_pharmacy", "hand_back_to_checkin"]})["passed"] is True


def test_tool_order_fails_when_reversed():
    t = [call("hand_back_to_checkin"), call("to_pharmacy")]
    assert only(t, {"tool_order": ["to_pharmacy", "hand_back_to_checkin"]})["passed"] is False


def test_tool_order_fails_when_a_name_is_missing():
    t = [call("to_pharmacy")]
    assert only(t, {"tool_order": ["to_pharmacy", "hand_back_to_checkin"]})["passed"] is False


# --- said_matches / said_never -------------------------------------------


def test_said_matches_searches_agent_turns_only():
    t = [user("good morning"), agent("Hello Margaret.")]
    assert only(t, {"said_matches": "(?i)good morning"})["passed"] is False
    assert only(t, {"said_matches": "(?i)hello"})["passed"] is True


def test_said_never_catches_an_unresolved_placeholder():
    t = [agent("Good morning {{first_name}}!")]
    r = only(t, {"said_never": "\\{\\{"})
    assert r["passed"] is False
    # The explanation has to name the offending line, or a failure is unusable.
    assert "first_name" in r["explanation"]


def test_said_never_passes_on_a_clean_transcript():
    t = [agent("Good morning Margaret!")]
    assert only(t, {"said_never": "\\{\\{"})["passed"] is True


def test_said_never_ignores_tool_traffic():
    # A placeholder inside a tool argument is not something the caller hears.
    t = [agent("Good morning."), call("log_mood", '{"note": "{{first_name}}"}')]
    assert only(t, {"said_never": "\\{\\{"})["passed"] is True


# --- ends_with -----------------------------------------------------------


def test_ends_with_tool_name_checks_the_last_invocation():
    t = [call("log_outcome"), call("end_call")]
    assert only(t, {"ends_with": "end_call"}, ending="the agent ended it")["passed"] is True


def test_ends_with_fails_when_another_tool_came_last():
    t = [call("end_call"), call("log_outcome")]
    assert only(t, {"ends_with": "end_call"}, ending="the agent ended it")["passed"] is False


def test_ends_with_caller_hangup():
    t = [agent("bye")]
    assert only(t, {"ends_with": "caller_hangup"}, ending=HUNG_UP)["passed"] is True
    assert only(t, {"ends_with": "caller_hangup"}, ending="the agent ended it")["passed"] is False


def test_ends_with_caller_hangup_fails_if_the_agent_hung_up_too():
    t = [call("end_call")]
    assert only(t, {"ends_with": "caller_hangup"}, ending=HUNG_UP)["passed"] is False


# --- turn_count_max ------------------------------------------------------


def test_turn_count_max_counts_user_turns():
    t = [user("a"), agent("b"), user("c"), agent("d")]
    assert only(t, {"turn_count_max": 2})["passed"] is True
    assert only(t, {"turn_count_max": 1})["passed"] is False


# --- framework behaviour -------------------------------------------------


def test_unknown_assertion_fails_rather_than_passing_silently():
    r = only([agent("hi")], {"tool_smelled": "log_mood"})
    assert r["passed"] is False
    assert "tool_smelled" in r["explanation"]


def test_empty_assertion_dict_fails():
    r = only([agent("hi")], {})
    assert r["passed"] is False


def test_results_come_back_in_order_one_per_assertion():
    t = [call("log_mood")]
    results = evaluate(t, [{"tool_called": "log_mood"}, {"tool_not_called": "log_mood"}], HUNG_UP)
    assert [r["passed"] for r in results] == [True, False]


def test_every_result_carries_the_judge_result_shape():
    results = evaluate([call("log_mood")], [{"tool_called": "log_mood"}], HUNG_UP)
    assert set(results[0]) == {"metric", "passed", "explanation"}
    assert isinstance(results[0]["metric"], str)
    assert results[0]["metric"]  # non-empty: it is the label the dashboard shows


def test_an_invalid_regex_fails_the_assertion_instead_of_raising():
    r = only([agent("hi")], {"said_matches": "(unclosed"})
    assert r["passed"] is False
    assert "regex" in r["explanation"].lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_assertions.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'arhiteq_api.services.assertions'`

- [ ] **Step 3: Implement the evaluator**

Create `backend/src/arhiteq_api/services/assertions.py`:

```python
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
"""

import json
import re
from typing import Any

# How `_Simulator` reports a caller who hung up (see `_user_turn`). `ends_with:
# caller_hangup` is the only assertion that reads the ending rather than the
# transcript, so this is the one string that has to stay in step with it.
CALLER_HUNG_UP = "the caller hung up"

# Tools that take the call away from the agent. `ends_with: caller_hangup` means
# the *caller* ended it, so neither of these may have run.
_TERMINAL_TOOLS = ("end_call", "transfer_call")


def _invocations(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in transcript if item.get("role") == "tool_call_invocation"]


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
    except (TypeError, ValueError):
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


def _check(transcript: list[dict[str, Any]], assertion: dict[str, Any], ending: str) -> tuple[bool, str]:
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
        if times is not None and len(matching) != times:
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
            hung_up = [c for c in invocations if c.get("name") in _TERMINAL_TOOLS]
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
    the dashboard needs no change. A bad regex fails its own assertion rather
    than the run: one malformed case must not take the batch down with it.
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
        results.append({"metric": _label(assertion), "passed": passed, "explanation": explanation})
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_assertions.py -v`
Expected: PASS — 27 tests

- [ ] **Step 5: Confirm nothing else broke and the tree is clean**

Run: `cd backend && uv run pytest -q`
Expected: the full suite passes; no test outside `tests/unit/test_assertions.py` changed behaviour.

Run: `cd backend && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/arhiteq_api/services/assertions.py backend/tests/unit/test_assertions.py
git commit -m "feat(simulation): grade a simulated call with mechanical assertions"
```

---

### Task 2: Carry `script` and `assertions` on a test case

Storage, schema, and the API surface. Nothing reads these yet — Task 3 wires them into the engine — but after this task a case can hold them and round-trip through the API.

**Files:**
- Modify: `backend/src/arhiteq_api/models.py:627-657` (`TestCaseDefinition`)
- Modify: `backend/src/arhiteq_api/main.py:60-86` (`_COLUMN_BACKFILLS`)
- Modify: `backend/src/arhiteq_api/api/agent_tests.py:51-68` (request schemas), `:87-103` (`_definition_to_dict`), `:213-233` (create), `:251-290` (update)
- Modify: `backend/tests/contract/test_simulation_tests.py`

**Interfaces:**
- Consumes: `evaluate` from Task 1 is not used here.
- Produces: `TestCaseDefinition.script: list[Any] | None` and `.assertions: list[Any] | None`; both appear in `_definition_to_dict` output as `script` and `assertions`, defaulting to `[]` when null.

- [ ] **Step 1: Write the failing contract test**

Append to `backend/tests/contract/test_simulation_tests.py`:

```python
async def test_case_carries_a_script_and_assertions(client):
    created = await _create_case(
        client,
        script=["Morning Clara.", "I took the Lipitor.", "No, that's all."],
        assertions=[
            {"tool_called": "log_medication_taken", "with": {"medication_name": "(?i)lipitor"}},
            {"tool_not_called": "purchase_offer"},
        ],
    )
    assert created["script"] == ["Morning Clara.", "I took the Lipitor.", "No, that's all."]
    assert created["assertions"][1] == {"tool_not_called": "purchase_offer"}

    case_id = created["test_case_definition_id"]
    got = await client.get(f"/get-test-case-definition/{case_id}", headers=AUTH_HEADERS)
    assert got.json()["script"] == created["script"]
    assert got.json()["assertions"] == created["assertions"]


async def test_a_case_without_them_reports_empty_lists(client):
    created = await _create_case(client)
    assert created["script"] == []
    assert created["assertions"] == []


async def test_script_and_assertions_are_updatable(client):
    case_id = (await _create_case(client))["test_case_definition_id"]
    res = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={"script": ["hello"], "assertions": [{"ends_with": "end_call"}]},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    assert res.json()["script"] == ["hello"]
    assert res.json()["assertions"] == [{"ends_with": "end_call"}]

    # Omitting them leaves them alone, the way every other optional field behaves.
    res = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={"name": "renamed"},
        headers=AUTH_HEADERS,
    )
    assert res.json()["script"] == ["hello"]
    assert res.json()["assertions"] == [{"ends_with": "end_call"}]


async def test_they_can_be_cleared_with_an_empty_list(client):
    case_id = (await _create_case(client, script=["hi"]))["test_case_definition_id"]
    res = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={"script": []},
        headers=AUTH_HEADERS,
    )
    assert res.json()["script"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/contract/test_simulation_tests.py -k "script or assertions" -x`
Expected: FAIL — `KeyError: 'script'`, because `_definition_to_dict` does not emit it.

- [ ] **Step 3: Add the columns to the model**

In `backend/src/arhiteq_api/models.py`, inside `TestCaseDefinition`, immediately after the `tool_mocks` column (line 653):

```python
    # Arhiteq extras, both nullable so every existing case keeps its behaviour.
    #
    # `script` replaces the improvising user simulator with literal caller
    # turns, played in order. That is what makes a run repeatable: the judge was
    # never the only noise source — a caller that improvises walks a different
    # path through the prompt every time, and grades a different conversation.
    #
    # `assertions` are graded mechanically (services/assertions.py) instead of
    # by the judge. A case carrying only assertions never calls a model to be
    # graded at all.
    script: Mapped[list[Any] | None] = mapped_column(JSON)
    assertions: Mapped[list[Any] | None] = mapped_column(JSON)
```

- [ ] **Step 4: Add the backfills**

In `backend/src/arhiteq_api/main.py`, append to `_COLUMN_BACKFILLS` (after the `qa_cohorts` entries, keeping the existing style):

```python
    ("test_case_definitions", "script", "JSON"),
    ("test_case_definitions", "assertions", "JSON"),
```

- [ ] **Step 5: Extend the request schemas and the serializer**

In `backend/src/arhiteq_api/api/agent_tests.py`, add to `TestCaseDefinitionRequest` (after `llm_model`, line 58):

```python
    script: list[str] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
```

Add to `UpdateTestCaseDefinitionRequest` (after `llm_model`, line 68):

```python
    script: list[str] | None = None
    assertions: list[dict[str, Any]] | None = None
```

Add to `_definition_to_dict`, after the `tool_mocks` entry:

```python
        # Arhiteq extras. Empty rather than null so a reader never has to
        # distinguish "no script" from "not supported by this version".
        "script": list(row.script or []),
        "assertions": list(row.assertions or []),
```

In `create_test_case_definition`, add to the `TestCaseDefinition(...)` constructor call, after `llm_model=body.llm_model,`:

```python
        script=list(body.script),
        assertions=list(body.assertions),
```

In `update_test_case_definition`, add after the `tool_mocks` block and before the `llm_model` block:

```python
    if body.script is not None:
        row.script = list(body.script)
    if body.assertions is not None:
        row.assertions = list(body.assertions)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/contract/test_simulation_tests.py -v`
Expected: PASS — the four new tests plus every pre-existing one in the file.

- [ ] **Step 7: Verify the backfill is idempotent against a real Postgres**

The dev stack's Postgres already holds a `test_case_definitions` table created before these columns existed, which is exactly the case the backfill is for.

```bash
cd ~/gofrolist/retell-clone && docker compose up -d --wait
cd backend && set -a; . ../.env; set +a
uv run python -c "
import asyncio
from arhiteq_api.db import get_engine
from arhiteq_api.main import _apply_column_backfills
from arhiteq_api.models import Base
async def go():
    e = get_engine()
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.run_sync(_apply_column_backfills)
        await c.run_sync(_apply_column_backfills)   # twice: must not raise
    await e.dispose()
    print('backfill idempotent OK')
asyncio.run(go())
"
```
Expected: `backfill idempotent OK`.

Leave the compose stack as you found it — if it was already running, do not stop it.

- [ ] **Step 8: Commit**

```bash
git add backend/src/arhiteq_api/models.py backend/src/arhiteq_api/main.py \
        backend/src/arhiteq_api/api/agent_tests.py backend/tests/contract/test_simulation_tests.py
git commit -m "feat(simulation): carry a caller script and assertions on a test case"
```

---

### Task 3: Play the script, grade the assertions

Wires Tasks 1 and 2 into the engine.

**Files:**
- Modify: `backend/src/arhiteq_api/services/simulation.py` — `definition_snapshot` (`:969-979`), `_Simulator.__init__`, `_user_turn` (`:842-866`), `_run_one` (`:1062-1179`)
- Modify: `backend/tests/unit/test_simulation.py`

**Interfaces:**
- Consumes: `evaluate(transcript, assertions, ending)` from Task 1; `TestCaseDefinition.script` / `.assertions` from Task 2.
- Produces: no new public functions. `_run_one` writes assertion results into the existing `job.metric_results`.

**Three behaviours to get right:**

1. **A scripted caller pops lines; an exhausted script hangs up.** Hanging up rather than merely stopping is load-bearing — `_Simulator.run` (`:893-902`) gives the agent a `_wrap_up_turn` only when the ending is `"the caller hung up"`, and that is what lets `tool_called: log_outcome` be graded on a prompt that says *say goodbye, log the disposition, then hang up*.
2. **Assertions and metrics compose.** A case may have both. Results are metrics-then-assertions in one list. The run passes only if every entry passed.
3. **A case with assertions and no metrics never calls the judge.** `_run_one` currently raises when a case has no metrics (`:1091-1092`) — that guard must now accept a case that has assertions instead, or every scripted case errors before it starts.

**Two pure helpers, so the grading logic is testable.** `_run_one` is not directly
tested anywhere today — every contract test stubs it wholesale
(`tests/contract/test_simulation_tests.py:171` and friends). Rather than leave
the new grading composition untested inside it, pull the two decisions it makes
into pure functions and call them from `_run_one`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_simulation.py`. The file's existing
`make_simulator` / `FakeModel` helpers do the work: `FakeModel` raises on an
unexpected extra call, so queueing only the agent's replies is itself the proof
that the user-simulator model was never asked for a line.

```python
# ------------------------------------------------------- scripted caller


async def test_a_scripted_caller_plays_its_lines_in_order(monkeypatch):
    """The scripted branch never reaches the user-simulator model.

    Only agent replies are queued — plus one for the wrap-up turn the hang-up
    earns. If the harness asked a model for a caller line, FakeModel would raise
    on the extra call, so the queue length is the assertion.
    """
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "How are you feeling?"},
            {"action": "speak", "content": "Glad to hear it."},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "",
            "metrics": [],
            "script": ["Morning, Clara.", "All fine here."],
        },
    )
    await sim.run()

    said = [item["content"] for item in sim.transcript if item["role"] == "user"]
    assert said == ["Morning, Clara.", "All fine here."]
    assert sim.ending == "the caller hung up"
    # The user-simulator prompt opens "You are role-playing a person…"; no
    # prompt sent to any model may contain it.
    assert not any("role-playing" in prompt for prompt in model.prompts)


async def test_an_exhausted_script_earns_the_agent_a_wrap_up_turn(monkeypatch):
    """A prompt that logs its disposition while hanging up is still graded."""
    sim, _ = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Take care."},
            {"action": "tool_call", "tool_name": "schedule_callback", "arguments": {}},
            {"action": "done"},
        ],
        definition={
            "user_prompt": "",
            "metrics": [],
            "script": ["That's everything, bye."],
            # Mocked so the harness does not spend a model call inventing a result.
            "tool_mocks": [
                {
                    "tool_name": "schedule_callback",
                    "input_match_rule": {"type": "any"},
                    "output": '{"ok": true}',
                }
            ],
        },
    )
    await sim.run()

    roles = [item["role"] for item in sim.transcript]
    assert "tool_call_invocation" in roles
    # The wrap-up work lands after the agent's last spoken line, exactly as it
    # does on a live call.
    last_spoken = max(i for i, role in enumerate(roles) if role == "agent")
    first_wrap_up_call = roles.index("tool_call_invocation")
    assert first_wrap_up_call > last_spoken


async def test_an_unscripted_case_still_improvises(monkeypatch):
    """The existing path is untouched: no script means the model plays the caller."""
    sim, model = make_simulator(
        monkeypatch,
        [
            {"action": "speak", "content": "Hello?"},
            {"action": "speak", "content": "How are you?"},
            {"action": "hangup", "reason": "done"},
            {"action": "done"},
        ],
        definition={"user_prompt": "You want a callback.", "metrics": []},
    )
    await sim.run()

    assert any("role-playing" in prompt for prompt in model.prompts)


async def test_a_script_of_one_line_still_hangs_up_rather_than_stalling(monkeypatch):
    sim, _ = make_simulator(
        monkeypatch,
        [{"action": "speak", "content": "Hello."}, {"action": "done"}],
        definition={"user_prompt": "", "metrics": [], "script": ["Hi."]},
    )
    await sim.run()
    assert sim.ending == "the caller hung up"


# ------------------------------------------------------------- grading


def test_combine_results_puts_metrics_before_assertions():
    judged = [{"metric": "m", "passed": True, "explanation": "j"}]
    asserted = [{"metric": "a", "passed": True, "explanation": "a"}]
    status, results, explanation = simulation.combine_results(judged, asserted)
    assert status == "pass"
    assert [r["metric"] for r in results] == ["m", "a"]
    assert "2 criteria passed" in explanation


def test_combine_results_fails_when_any_entry_failed():
    judged = [{"metric": "m", "passed": True, "explanation": "j"}]
    asserted = [{"metric": "a", "passed": False, "explanation": "nope"}]
    status, results, explanation = simulation.combine_results(judged, asserted)
    assert status == "fail"
    assert "a" in explanation and "nope" in explanation


def test_combine_results_handles_assertions_only():
    status, results, _ = simulation.combine_results(
        [], [{"metric": "a", "passed": True, "explanation": "ok"}]
    )
    assert status == "pass"
    assert len(results) == 1


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({"metrics": ["does the thing"]}, True),
        ({"assertions": [{"tool_called": "log_mood"}]}, True),
        ({"metrics": ["x"], "assertions": [{"tool_called": "y"}]}, True),
        ({}, False),
        ({"metrics": [], "assertions": []}, False),
        # Whitespace-only criteria have never counted as gradable.
        ({"metrics": ["   "]}, False),
        # …but a whitespace-only metric alongside a real assertion does.
        ({"metrics": ["  "], "assertions": [{"ends_with": "end_call"}]}, True),
    ],
)
def test_has_gradable_criteria(snapshot, expected):
    assert simulation.has_gradable_criteria(snapshot) is expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_simulation.py -k "script or assertion or judge" -x`
Expected: FAIL.

- [ ] **Step 3: Carry the fields through the snapshot**

In `definition_snapshot` (`simulation.py:969-979`), add:

```python
        "script": list(definition.script or []),
        "assertions": list(definition.assertions or []),
```

A run executes from the snapshot frozen at run time, so a case edited mid-batch still runs as it was queued.

- [ ] **Step 4: Add the scripted branch to `_user_turn`**

`_Simulator.__init__` already stores the whole case as `self._definition`, so the script is reachable without a new constructor argument. Add an index to `__init__` beside `self.transcript`:

```python
        # How far through a scripted caller's lines this run has got. Unused by
        # an improvising case.
        self._script_position = 0
```

Then at the top of `_user_turn`, before it reads the scenario:

```python
        script = [str(line) for line in (self._definition.get("script") or [])]
        if script:
            # A scripted caller says exactly what the case wrote, in order. This
            # is the half of determinism the assertions cannot supply: grading is
            # only repeatable if the conversation being graded is the same one.
            if self._script_position >= len(script):
                # Out of lines. Hanging up rather than falling silent is what
                # earns the agent its wrap-up turn (see `run`), so a prompt that
                # logs its disposition while ending the call is still graded.
                return "the caller hung up"
            line = script[self._script_position]
            self._script_position += 1
            self.transcript.append({"role": "user", "content": line})
            return None
```

- [ ] **Step 5: Add the two pure helpers**

In `backend/src/arhiteq_api/services/simulation.py`, immediately after `_explain`
(which is at `:961-966` and which `combine_results` uses):

```python
def has_gradable_criteria(snapshot: Mapping[str, Any]) -> bool:
    """Whether a case has anything to grade at all.

    Assertions count. A scripted case commonly carries assertions and no judged
    criteria — that is the point of it — and the pre-existing guard checked only
    `metrics`, so without this every scripted case would error before it ran.
    """
    if [m for m in (snapshot.get("metrics") or []) if str(m).strip()]:
        return True
    return bool(snapshot.get("assertions"))


def combine_results(
    judged: list[dict[str, Any]], asserted: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]], str]:
    """One verdict over judged criteria and mechanical assertions together.

    Metrics first, then assertions, matching the order a case declares them, so
    the run drawer reads top-to-bottom the way the case does. A run passes only
    if everything passed — an assertion is not a softer kind of criterion.
    """
    results = list(judged) + list(asserted)
    status = "pass" if all(r["passed"] for r in results) else "fail"
    return status, results, _explain(status, results)
```

- [ ] **Step 6: Wire them into `_run_one`**

Add the import at the top of the module, replacing the existing
`from . import knowledge, versions`:

```python
from . import assertions, knowledge, versions
```

Replace the no-metrics guard (`:1091-1092`):

```python
        # A case that grades nothing can only ever end in `error`, so don't
        # spend a whole simulated call finding out.
        if not has_gradable_criteria(snapshot):
            raise RuntimeError("This test case has no success criteria to grade.")
```

Replace the grading call (`:1157-1159`):

```python
        await simulator.run()
        # Assertions cost nothing, so grade them first — a case with no judged
        # criteria then never reaches a model to be graded at all.
        asserted = assertions.evaluate(
            simulator.transcript, snapshot.get("assertions") or [], simulator.ending
        )
        judged: list[dict[str, Any]] = []
        if [m for m in (snapshot.get("metrics") or []) if str(m).strip()]:
            _, judged = await simulator.judge()
        status, results, explanation = combine_results(judged, asserted)
```

`judge()` returns `(status, results)`; its status is discarded because
`combine_results` computes the verdict over the merged list.

`Mapping` is already imported at `simulation.py:42`; `Any` at `:43`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_simulation.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 8: Run the whole suite and the linters**

Run: `cd backend && uv run pytest -q`
Expected: the full suite passes.

Run: `cd backend && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.

- [ ] **Step 9: Drive it end to end against the local stack**

A scripted case with assertions, run through the real API, is the only thing that proves the three pieces meet. Needs Gemini credentials (`GOOGLE_API_KEY` or Vertex ADC) in `.env`.

```bash
cd ~/gofrolist/retell-clone && docker compose up -d --wait
cd backend && set -a; . ../.env; set +a
uv run python -m arhiteq_api.seed --api-key key_scripted_demo --workspace-name "scripted demo"
uv run uvicorn arhiteq_api.main:app --port 8080   # leave running in another shell
```

Then create an LLM with a small prompt that must call a tool, create a scripted case against it with one `tool_called` assertion and no metrics, run a batch, and poll the run. Report the resulting `metric_results` verbatim — it must contain the assertion's label, `passed`, and an explanation, with no judge entry.

Stop the uvicorn process when finished. Leave the compose stack as you found it.

- [ ] **Step 10: Commit**

```bash
git add backend/src/arhiteq_api/services/simulation.py backend/tests/unit/test_simulation.py
git commit -m "feat(simulation): play a scripted caller and grade assertions without the judge"
```

---

## Done when (Phase A)

- `cd backend && uv run pytest` passes, including ~26 new assertion tests, 4 new contract tests, and 4 scripted-caller plus 9 grading tests in the simulation unit file.
- A case with `script` and `assertions` runs end to end through the API and returns assertion results in `metric_results`, with no judge call.
- A case with neither field behaves exactly as before.
- The dashboard renders assertion results with **no frontend change** — verify by eye in the Simulation tab's run drawer.
- `docs/API_COVERAGE.md` and `docs/AGENT_VERSIONING.md` need no change; if `docs/INTERNAL_API.md` documents the test-case shape, add the two fields there.

## Phase B — the Clara cases and the gate (outline only)

**Does not start until Phase A is merged and released.** Phase B is authored against the shipped case format, and gets its own plan written at that point. Specifying it now would repeat the mistake the previous plan in this series made — its code blocks were written before the code existed and several turned out to be wrong.

Shape it will take, in `usan-retirement-backend`:

1. **Case format and runner.** `prompts/clara/tests/cases/<block>/*.yaml` carrying `name`, `agent`, `variables`, `tools`, `script`, `assert`. A runner in `prompts/clara/tests/runner/` syncs them into an Arhiteq workspace via the API — reusing `prompts/clara/lint/http.js` and `seed.js`'s workspace — triggers a batch, polls, and reports. `{{today}}` expands at sync time so a committed case does not go stale.
2. **~15 cases** over the flows that break most: the morning and evening happy paths, each hard stop, the crisis paths, the closing gate, confirm-before-save, and the day-7-offer-versus-paying-member case that PR #38 fixed by hand.
3. **The gate.** A CI job on PRs touching `prompts/clara/`, using repeat-on-failure (run once, re-run only failures twice, red only if all three fail) rather than `--repeat 3` on everything.

Open questions Phase B must answer, which Phase A's outcome will inform:

- How the runner authenticates in CI, and which workspace it targets — this needs a reachable Arhiteq, which is the same unsolved problem that left parity out of CI in the previous PR. It may be that the layer-1 gate can only run locally and pre-publish until an Arhiteq instance is reachable from CI.
- Whether 15 scripted cases actually hold their path stable across runs at temperature 0, measured rather than assumed.
- Which handoff assertions the real cases need, which decides whether `swapped_to` / `agent_at_end` / `no_reask` are worth building.
