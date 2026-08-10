"""The case format, tested for the shapes that run and mean nothing.

Every rejection below describes a case that would otherwise complete, produce a
WAV, produce a report, and be wrong — which is worse than a crash, because a
crash gets fixed.
"""

from __future__ import annotations

import json
from pathlib import Path

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


# --- expectations --------------------------------------------------------


def test_expectations_parse_and_keep_their_shape():
    case = parse_case(
        {
            **GOOD,
            "expect": [
                {"heard": "good morning"},
                {"never_heard": "first name"},
                {"tool_called": "log_mood", "with": {"mood_score": "^4$"}},
                {"tool_not_called": "purchase_offer"},
            ],
        }
    )
    assert case.expect == (
        {"heard": "good morning"},
        {"never_heard": "first name"},
        {"tool_called": "log_mood", "with": {"mood_score": "^4$"}},
        {"tool_not_called": "purchase_offer"},
    )


def test_a_case_with_no_expectations_is_allowed():
    # The global rules still run. A smoke case proving the plumbing has nothing
    # of its own to assert and should not be forced to invent something.
    assert parse_case(GOOD).expect == ()


def test_a_pattern_hunting_for_braces_is_refused():
    # Layer 1's `said_never: '\{\{'` translated here would never fire: ASR of a
    # model reading a placeholder aloud returns the words, never the braces.
    # Accepting it would add a case that permanently reports clean.
    with pytest.raises(CaseError, match="first name"):
        parse_case({**GOOD, "expect": [{"never_heard": "\\{\\{"}]})


def test_a_pattern_with_an_apostrophe_is_refused():
    # Normalisation deletes apostrophes, so "it's clara" matches nothing —
    # including a call where the agent said exactly that.
    with pytest.raises(CaseError, match="written without punctuation|removed before matching"):
        parse_case({**GOOD, "expect": [{"heard": "it's clara"}]})


def test_an_invalid_regex_is_refused_at_load_rather_than_mid_call():
    with pytest.raises(CaseError, match="not a valid regex"):
        parse_case({**GOOD, "expect": [{"heard": "good (morning"}]})


def test_an_expectation_naming_nothing_is_refused():
    with pytest.raises(CaseError, match="exactly one"):
        parse_case({**GOOD, "expect": [{"sounded_warm": "very"}]})


def test_an_expectation_naming_two_checks_is_refused():
    # Which one wins would be an implementation detail, and the other would be
    # silently dropped.
    with pytest.raises(CaseError, match="exactly one"):
        parse_case({**GOOD, "expect": [{"heard": "morning", "never_heard": "evening"}]})


def test_with_on_a_speech_expectation_is_refused():
    with pytest.raises(CaseError, match="belongs to tool_called"):
        parse_case({**GOOD, "expect": [{"heard": "morning", "with": {"a": "b"}}]})


def test_with_on_tool_not_called_is_refused():
    # It reads as "not called with these arguments" and does not mean that.
    with pytest.raises(CaseError, match="takes no 'with'"):
        parse_case(
            {**GOOD, "expect": [{"tool_not_called": "send_family_sms", "with": {"phone": "5"}}]}
        )


def test_a_with_pattern_is_checked_like_any_other():
    with pytest.raises(CaseError, match="with.phone"):
        parse_case(
            {**GOOD, "expect": [{"tool_called": "send_family_sms", "with": {"phone": "(+1"}}]}
        )


def test_times_parses_on_a_tool_expectation():
    case = parse_case({**GOOD, "expect": [{"tool_called": "log_mood", "times": 1}]})
    assert case.expect == ({"tool_called": "log_mood", "times": 1},)


def test_times_must_be_a_positive_whole_number():
    for bad in (0, -1, 1.5, True, "1"):
        with pytest.raises(CaseError, match="times must be a positive whole number"):
            parse_case({**GOOD, "expect": [{"tool_called": "log_mood", "times": bad}]})


def test_times_on_a_speech_expectation_is_refused():
    with pytest.raises(CaseError, match="belongs to tool_called"):
        parse_case({**GOOD, "expect": [{"heard": "morning", "times": 2}]})


def test_times_on_tool_not_called_is_refused():
    # "not called twice" is not what it means, and it would be ignored.
    with pytest.raises(CaseError, match="takes no 'with' or 'times'"):
        parse_case({**GOOD, "expect": [{"tool_not_called": "purchase_offer", "times": 2}]})


def test_an_empty_with_is_refused():
    # An empty map matches every invocation, so the assertion silently weakens
    # to "the tool was called at all".
    with pytest.raises(CaseError, match="non-empty object"):
        parse_case({**GOOD, "expect": [{"tool_called": "log_mood", "with": {}}]})


# --- tool mocks ----------------------------------------------------------


def test_tool_results_must_be_json_strings():
    case = parse_case({**GOOD, "tools": {"log_mood": '{"ok": true}'}})
    assert case.tools == {"log_mood": '{"ok": true}'}


def test_a_tool_result_that_is_not_json_is_refused():
    # It reaches the model as a broken payload and steers every turn after it,
    # so the call grades the harness rather than the prompt.
    with pytest.raises(CaseError, match="not valid JSON"):
        parse_case({**GOOD, "tools": {"log_mood": "ok"}})


def test_a_tool_result_given_as_an_object_is_refused():
    # The sink answers with the string verbatim; an object here would be a
    # different serialisation than the one the case author read.
    with pytest.raises(CaseError, match="JSON STRING"):
        parse_case({**GOOD, "tools": {"log_mood": {"ok": True}}})


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


# --- inherited variables -------------------------------------------------


def test_a_case_inherits_the_shared_variables_and_overrides_what_it_needs(tmp_path):
    (tmp_path / "_shared.json").write_text(
        json.dumps({"first_name": "Margaret", "time_of_day": "morning"})
    )
    path = tmp_path / "one.case.json"
    path.write_text(
        json.dumps(
            {**GOOD, "variables_from": "_shared.json", "variables": {"time_of_day": "evening"}}
        )
    )
    # The override is the sentence the case is making; the rest is context it
    # would be noise to repeat.
    assert load_case(path).variables == {"first_name": "Margaret", "time_of_day": "evening"}


def test_a_missing_shared_file_is_refused(tmp_path):
    path = tmp_path / "one.case.json"
    path.write_text(json.dumps({**GOOD, "variables_from": "_nope.json"}))
    with pytest.raises(CaseError, match="does not exist"):
        load_case(path)


def test_the_shared_file_may_carry_its_own_notes(tmp_path):
    # JSON has nowhere else to put the reasoning behind a value, and the shared
    # file is exactly where that reasoning belongs.
    (tmp_path / "_shared.json").write_text(
        json.dumps({"$comment": ["why these values"], "first_name": "Margaret"})
    )
    path = tmp_path / "one.case.json"
    path.write_text(json.dumps({**GOOD, "variables_from": "_shared.json"}))
    assert load_case(path).variables == {"first_name": "Margaret"}


def test_an_inherited_variable_is_type_checked_like_an_inline_one(tmp_path):
    (tmp_path / "_shared.json").write_text(json.dumps({"meds": ["Lipitor"]}))
    path = tmp_path / "one.case.json"
    path.write_text(json.dumps({**GOOD, "variables_from": "_shared.json"}))
    with pytest.raises(CaseError, match="'meds' must be a string"):
        load_case(path)


def test_variables_from_needs_a_file_to_resolve_against():
    with pytest.raises(CaseError, match="loaded from a file"):
        parse_case({**GOOD, "variables_from": "_shared.json"})


# --- the caseload that ships ---------------------------------------------


def test_every_shipped_case_loads():
    # The refusals above are only worth having if the cases in the repo pass
    # them. A case that stopped loading would otherwise be discovered by
    # someone with a stack and forty seconds of call time already spent.
    cases = discover(Path(__file__).parent / "cases")
    assert len(cases) >= 6
    assert len({case.name for case in cases}) == len(cases)


def test_every_shipped_case_names_a_file_that_matches_it():
    for path in sorted((Path(__file__).parent / "cases").glob("*.case.json")):
        # A report names the case; a person then has to find the file. Letting
        # those drift apart costs a search every time anyone reads a failure.
        assert load_case(path).name == path.name.removesuffix(".case.json")


def test_no_shipped_clara_case_leaves_its_tools_unmocked_by_accident():
    # A tool with no canned result gets an invented payload, which steers every
    # turn after it. That is tolerable for a tool a case never expected to
    # reach, but a tool the case ASSERTS is one the case knows about, and it
    # should say what comes back.
    for path in sorted((Path(__file__).parent / "cases").glob("*.case.json")):
        case = load_case(path)
        asserted = {
            expectation["tool_called"]
            for expectation in case.expect
            if "tool_called" in expectation
        }
        # Built-ins and swaps are executed by the platform and have no payload
        # anyone can supply.
        supplied = set(case.tools) | {"to_pharmacy", "to_services", "to_billing", "to_account"}
        assert asserted <= supplied, f"{case.name} asserts {asserted - supplied} without a mock"


def test_discovery_is_ordered_so_a_suite_runs_the_same_way_everywhere(tmp_path):
    for name in ("b-second", "a-first", "c-third"):
        (tmp_path / f"{name}.case.json").write_text(json.dumps({**GOOD, "name": name}))
    (tmp_path / "notes.md").write_text("not a case")
    assert [case.name for case in discover(tmp_path)] == ["a-first", "b-second", "c-third"]
