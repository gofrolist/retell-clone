"""The case format, tested for the shapes that run and mean nothing.

Every rejection below describes a case that would otherwise complete, produce a
WAV, produce a report, and be wrong — which is worse than a crash, because a
crash gets fixed.
"""

from __future__ import annotations

import json

import pytest

from audio.caseload import (
    DEFAULT_CALLER_VOICE,
    DEFAULT_MAX_CALL_S,
    DEFAULT_SETTLE_S,
    CaseError,
    discover,
    load_case,
    parse_case,
)

GOOD = {
    "name": "checkin-greets-once",
    "agent": "clara-checkin",
    "variables": {"first_name": "Margaret"},
    "script": ["I'm alright, thanks.", "No, that's everything."],
}


def test_a_good_case_parses_with_the_defaults_filled_in():
    case = parse_case(GOOD)
    assert case.name == "checkin-greets-once"
    assert case.script == ("I'm alright, thanks.", "No, that's everything.")
    assert case.voice == DEFAULT_CALLER_VOICE
    assert case.settle_s == DEFAULT_SETTLE_S
    assert case.max_call_s == DEFAULT_MAX_CALL_S


def test_a_misspelled_key_is_an_error_not_a_shrug():
    # `varibles` drops sixteen dynamic variables in silence. The prompt then
    # renders with its placeholder defaults and the call greets someone the
    # case never described — the exact shape of a bug that has already reached
    # production once.
    typo = {k: v for k, v in GOOD.items() if k != "variables"}
    typo["varibles"] = {"first_name": "Margaret"}
    with pytest.raises(CaseError, match="varibles"):
        parse_case(typo)


def test_a_case_with_nothing_to_say_is_refused():
    # A silent caller turns the case into the agent monologuing to a dead line.
    # It completes, it finds nothing, and it reports a clean call.
    with pytest.raises(CaseError, match="non-empty 'script'"):
        parse_case({**GOOD, "script": []})


def test_a_blank_script_line_is_refused():
    with pytest.raises(CaseError, match="line 2"):
        parse_case({**GOOD, "script": ["Morning.", "   "]})


def test_a_case_without_an_agent_is_refused():
    with pytest.raises(CaseError, match="'agent'"):
        parse_case({k: v for k, v in GOOD.items() if k != "agent"})


def test_a_non_string_variable_is_refused():
    # The prompt interpolates these into a sentence. A list renders as its
    # Python repr mid-sentence, and the model reads it aloud.
    with pytest.raises(CaseError, match="'meds' must be a string"):
        parse_case({**GOOD, "variables": {"meds": ["Lipitor", "Metformin"]}})


def test_a_zero_timeout_is_refused():
    # Zero means every turn gives up before the agent has drawn breath, and the
    # recording is nothing but the caller's own lines.
    with pytest.raises(CaseError, match="reply_timeout_s"):
        parse_case({**GOOD, "reply_timeout_s": 0})


def test_a_negative_limit_is_refused():
    with pytest.raises(CaseError, match="max_call_s"):
        parse_case({**GOOD, "max_call_s": -30})


def test_a_boolean_is_not_accepted_as_a_number():
    # `True` is an int in Python and would sail through a numeric check as one
    # second — a settle time nobody wrote and nobody can see in the case file.
    with pytest.raises(CaseError, match="settle_s"):
        parse_case({**GOOD, "settle_s": True})


def test_a_case_that_is_not_an_object_is_refused():
    with pytest.raises(CaseError, match="JSON object"):
        parse_case(["not", "a", "case"])


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(CaseError, match="does not exist"):
        load_case(tmp_path / "nope.case.json")


def test_broken_json_says_where(tmp_path):
    path = tmp_path / "broken.case.json"
    path.write_text("{oops")
    with pytest.raises(CaseError, match="not valid JSON"):
        load_case(path)


def test_a_file_round_trips(tmp_path):
    path = tmp_path / "one.case.json"
    path.write_text(json.dumps(GOOD))
    assert load_case(path).name == GOOD["name"]


def test_discovery_is_ordered_so_a_suite_runs_the_same_way_everywhere(tmp_path):
    for name in ("b-second", "a-first", "c-third"):
        (tmp_path / f"{name}.case.json").write_text(json.dumps({**GOOD, "name": name}))
    (tmp_path / "notes.md").write_text("not a case")
    assert [case.name for case in discover(tmp_path)] == ["a-first", "b-second", "c-third"]
