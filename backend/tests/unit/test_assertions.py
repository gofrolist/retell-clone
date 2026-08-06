"""The mechanical half of grading a simulated call.

Every test builds a transcript literally — no database, no LLM, no fixtures —
because that is the property the whole determinism argument rests on.
"""

import pytest

from arhiteq_api.services.assertions import evaluate

HUNG_UP = "the caller hung up"


def agent(text):
    return {"role": "agent", "content": text}


def user(text):
    return {"role": "user", "content": text}


def call(name, arguments=None, tool_type=None):
    """One invocation. `tool_type` left out is a transcript recorded before the
    type was written down — the shape every stored run still has."""
    entry = {
        "role": "tool_call_invocation",
        "name": name,
        "arguments": arguments if arguments is not None else "{}",
        "tool_call_id": f"tool_{name}",
    }
    if tool_type is not None:
        entry["type"] = tool_type
    return entry


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
    assert (
        only(
            t, {"tool_called": "log_medication_taken", "with": {"medication_name": "(?i)lipitor"}}
        )["passed"]
        is True
    )


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
    r = only(
        t, {"tool_called": "send_family_sms", "with": {"phone": "\\+1555", "message": "checking"}}
    )
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
    # No `type` on the entry: an older transcript, matched by name.
    t = [call("end_call")]
    assert only(t, {"ends_with": "caller_hangup"}, ending=HUNG_UP)["passed"] is False


def test_ends_with_caller_hangup_sees_a_hang_up_tool_the_config_renamed():
    # The agent config is free to call its `end_call` tool anything; the harness
    # decides by type. Grading by name alone would report "the caller hung up"
    # for a call the agent ended — a silently wrong pass.
    t = [call("hang_up", tool_type="end_call")]
    r = only(t, {"ends_with": "caller_hangup"}, ending=HUNG_UP)
    assert r["passed"] is False
    assert "hang_up" in r["explanation"]


def test_ends_with_caller_hangup_trusts_the_type_over_the_name():
    # A custom tool that happens to be named `end_call` did not end anything.
    t = [call("end_call", tool_type="custom")]
    assert only(t, {"ends_with": "caller_hangup"}, ending=HUNG_UP)["passed"] is True


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


@pytest.mark.parametrize(
    "assertion",
    [
        # A count written as a string: compared against an int by `>`.
        {"turn_count_max": "2"},
        # A `with` that is a string rather than a map of argument patterns.
        {"tool_called": "log_mood", "with": "nope"},
        # A count that is no kind of number at all.
        {"tool_called": "log_mood", "times": "twice"},
    ],
)
def test_a_malformed_assertion_fails_instead_of_raising(assertion):
    """A case is hand-written JSON; every way of being wrong has to land here.

    Before this, each of these escaped `evaluate` and marked the whole run
    `error`, discarding every other assertion's verdict along with it.
    """
    r = only([user("a"), call("log_mood"), agent("hi")], assertion)
    assert r["passed"] is False
    assert "could not be evaluated" in r["explanation"]


def test_a_malformed_assertion_does_not_take_the_other_verdicts_with_it():
    results = evaluate(
        [call("log_mood")],
        [{"turn_count_max": "2"}, {"tool_called": "log_mood"}],
        HUNG_UP,
    )
    assert [r["passed"] for r in results] == [False, True]


def test_times_written_as_a_string_counts_as_the_number_it_says():
    t = [call("log_mood")]
    assert only(t, {"tool_called": "log_mood", "times": "1"})["passed"] is True
    assert only(t, {"tool_called": "log_mood", "times": "2"})["passed"] is False
