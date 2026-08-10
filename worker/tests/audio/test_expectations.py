"""Per-case expectations, checked without a call.

Every transcript here is built literally. That is the property the layer rests
on: a case's verdict has to be a function of what was heard and what was
called, and nothing else, or a red run is not evidence of anything.
"""

from __future__ import annotations

import json

from audio.analysis import AGENT, CALLER, Segment
from audio.expectations import ToolCall, check, matches, tool_calls

CALL_END = 42.0


def agent(text, start=1.0):
    return Segment(start=start, end=start + 2.0, text=text, speaker=AGENT)


def caller(text, start=0.0):
    return Segment(start=start, end=start + 1.0, text=text, speaker=CALLER)


def invocation(name, arguments=None):
    return {
        "role": "tool_call_invocation",
        "name": name,
        "arguments": json.dumps(arguments) if arguments is not None else "{}",
        "tool_call_id": f"tool_{name}",
    }


def only(expectation, *, segments=(), calls=()):
    """One expectation's findings."""
    return check([expectation], segments=list(segments), calls=list(calls), call_end=CALL_END)


# --- heard ---------------------------------------------------------------


def test_heard_passes_on_a_matching_utterance():
    assert not only({"heard": "good morning"}, segments=[agent("Good morning, Margaret.")])


def test_heard_fails_and_names_the_pattern():
    findings = only({"heard": "good morning"}, segments=[agent("Hello there.")])
    assert len(findings) == 1
    assert "good morning" in findings[0].detail
    # Placed at the end of the call: nothing about "never said" happened at a
    # moment, and a report reads as a walk through the recording.
    assert findings[0].at == CALL_END


def test_heard_ignores_punctuation_and_case_the_transcriber_invented():
    # The same sentence, transcribed three ways. None of the differences are
    # facts about the prompt, so none of them may change the verdict.
    for text in ["It's Clara.", "its clara", "IT'S CLARA!"]:
        assert not only({"heard": "its clara"}, segments=[agent(text)]), text


def test_a_pattern_with_a_capital_still_matches_normalised_speech():
    # Normalisation lower-cases the haystack, so without IGNORECASE a pattern
    # written the way a person would write it could never match.
    assert not only({"heard": "Lipitor"}, segments=[agent("did you take your lipitor")])


def test_heard_never_matches_the_callers_own_lines():
    # The caller's words are the harness's script. A case that passed because
    # the harness said the thing itself would be worthless.
    assert only({"heard": "good morning"}, segments=[caller("Good morning Clara.")])


# --- never_heard ---------------------------------------------------------


def test_never_heard_passes_when_absent():
    assert not only({"never_heard": "first name"}, segments=[agent("Good morning, Margaret.")])


def test_never_heard_reports_the_moment_it_was_said():
    # This one DID happen at a moment, and the moment is the point: the whole
    # promise of the audio layer is that a failure hands you the seconds to
    # listen to.
    findings = only(
        {"never_heard": "first name"},
        segments=[agent("Good morning, first name, it's Clara.", start=3.5)],
    )
    assert len(findings) == 1
    assert findings[0].at == 3.5


def test_never_heard_reports_every_offending_utterance():
    findings = only(
        {"never_heard": "first name"},
        segments=[agent("Good morning first name.", 2.0), agent("Bye first name.", 20.0)],
    )
    assert [f.at for f in findings] == [2.0, 20.0]


# --- tool_called ---------------------------------------------------------


def test_tool_called_passes_when_the_record_shows_it():
    assert not only({"tool_called": "log_mood"}, calls=[ToolCall("log_mood", {})])


def test_tool_called_fails_when_it_never_happened():
    findings = only({"tool_called": "log_mood"}, calls=[ToolCall("log_outcome", {})])
    assert len(findings) == 1
    assert "log_mood" in findings[0].detail


def test_tool_called_with_matches_one_invocations_arguments():
    calls = [ToolCall("log_medication_taken", {"medication_name": "Lipitor 20mg"})]
    assert not only(
        {"tool_called": "log_medication_taken", "with": {"medication_name": "lipitor"}},
        calls=calls,
    )


def test_tool_called_with_needs_all_of_it_on_one_call():
    # Half a match on each of two calls is not a match. A coupon texted to one
    # number with a message written for another passes an assertion that lets
    # two calls share the credit.
    calls = [
        ToolCall("send_family_sms", {"phone": "+15555550123", "message": "hello"}),
        ToolCall("send_family_sms", {"phone": "+19998887777", "message": "your coupon"}),
    ]
    findings = only(
        {"tool_called": "send_family_sms", "with": {"phone": "5555550123", "message": "coupon"}},
        calls=calls,
    )
    assert len(findings) == 1


def test_tool_called_with_reads_non_string_arguments_as_their_text():
    calls = [ToolCall("log_mood", {"mood_score": 4, "slept_well": True})]
    assert not only({"tool_called": "log_mood", "with": {"mood_score": "^4$"}}, calls=calls)


def test_tool_called_with_fails_on_a_missing_argument_rather_than_crashing():
    findings = only(
        {"tool_called": "log_mood", "with": {"mood_score": "."}},
        calls=[ToolCall("log_mood", {})],
    )
    assert len(findings) == 1


def test_tool_called_times_is_exact():
    # The Live-model bug: a turn aborted after its tools ran, then regenerated,
    # logging the same mood and the same outcome twice each. Every assertion
    # that only asks whether the tool ran called that a clean call.
    twice = [ToolCall("log_mood", {}), ToolCall("log_mood", {})]
    assert not only({"tool_called": "log_mood", "times": 2}, calls=twice)
    findings = only({"tool_called": "log_mood", "times": 1}, calls=twice)
    assert len(findings) == 1
    assert "2x" in findings[0].detail


def test_tool_called_times_still_fails_when_never_called():
    findings = only({"tool_called": "log_mood", "times": 1}, calls=[])
    assert len(findings) == 1
    assert "never called" in findings[0].detail


def test_tool_called_times_and_with_are_both_checked():
    calls = [ToolCall("log_mood", {"mood_score": 4}), ToolCall("log_mood", {"mood_score": 4})]
    assert only({"tool_called": "log_mood", "times": 1, "with": {"mood_score": "^4$"}}, calls=calls)


# --- tool_not_called -----------------------------------------------------


def test_tool_not_called_passes_when_absent():
    assert not only({"tool_not_called": "purchase_offer"}, calls=[ToolCall("log_mood", {})])


def test_tool_not_called_fails_and_counts():
    findings = only(
        {"tool_not_called": "purchase_offer"},
        calls=[ToolCall("purchase_offer", {}), ToolCall("purchase_offer", {})],
    )
    assert len(findings) == 1
    assert "2x" in findings[0].detail


# --- reading the platform record -----------------------------------------


def test_tool_calls_reads_invocations_in_order():
    record = {
        "transcript_with_tool_calls": [
            {"role": "agent", "content": "one moment"},
            invocation("to_pharmacy"),
            {"role": "tool_call_result", "name": "to_pharmacy", "content": "{}"},
            invocation("medication_price_lookup", {"drug": "Lipitor"}),
        ]
    }
    calls = tool_calls(record)
    assert [c.name for c in calls] == ["to_pharmacy", "medication_price_lookup"]
    assert calls[1].arguments == {"drug": "Lipitor"}


def test_a_record_with_no_tool_field_yields_nothing_rather_than_passing():
    # The failure mode this guards: a case asserts a tool, the evidence is
    # missing, and the run reports clean. Absent evidence has to read as "the
    # agent did not call it", which fails the expectation.
    assert tool_calls({"transcript_object": [{"role": "agent", "content": "hi"}]}) == []
    assert only({"tool_called": "log_mood"}, calls=tool_calls({}))


def test_malformed_arguments_are_a_call_that_happened_with_nothing_to_match():
    record = {
        "transcript_with_tool_calls": [
            {"role": "tool_call_invocation", "name": "log_mood", "arguments": "not json"}
        ]
    }
    calls = tool_calls(record)
    assert [c.name for c in calls] == ["log_mood"]
    assert not only({"tool_called": "log_mood"}, calls=calls)
    assert only({"tool_called": "log_mood", "with": {"anything": "."}}, calls=calls)


# --- the whole set -------------------------------------------------------


def test_findings_come_back_in_call_order():
    findings = check(
        [
            {"tool_called": "log_mood"},
            {"never_heard": "first name"},
        ],
        segments=[agent("morning first name", start=5.0)],
        calls=[],
        call_end=CALL_END,
    )
    assert [f.at for f in findings] == [5.0, CALL_END]


def test_an_expectation_nothing_understands_fails_rather_than_passes():
    findings = only({"sounded_friendly": "very"})
    assert len(findings) == 1
    assert findings[0].rule == "unknown_expectation"


def test_matches_is_the_shared_definition_of_a_pattern_being_present():
    assert matches("its clara", "It's Clara.")
    assert not matches("its clara", "This is Clara.")


# --- a call that ended before the script did ------------------------------


def test_a_positive_expectation_on_a_truncated_call_says_it_is_unproven():
    # The agent hung up two lines early. "The crisis number was never said" is
    # not a finding about the prompt when the call never reached the line that
    # would have prompted it — and the finding itself has to say so, because
    # the warning that used to say it only ever reached stdout.
    (finding,) = check(
        [{"heard": "988"}],
        segments=[agent("Good morning.")],
        calls=[],
        call_end=4.0,
        unspoken_lines=2,
    )
    assert finding.rule == "heard"
    assert "unproven" in finding.detail
    assert "2 caller line(s)" in finding.detail


def test_a_negative_expectation_is_never_unproven():
    # `never_heard` and `tool_not_called` are about something that DID happen
    # in the part of the call that ran, and a short call cannot make them true
    # by accident.
    (heard,) = check(
        [{"never_heard": "first name"}],
        segments=[agent("Good morning first name.")],
        calls=[],
        call_end=4.0,
        unspoken_lines=2,
    )
    (called,) = check(
        [{"tool_not_called": "purchase_offer"}],
        segments=[],
        calls=[ToolCall(name="purchase_offer", arguments={})],
        call_end=4.0,
        unspoken_lines=2,
    )
    assert "unproven" not in heard.detail
    assert "unproven" not in called.detail


def test_a_call_that_ran_to_the_end_says_nothing_about_unproven():
    (finding,) = check(
        [{"heard": "988"}],
        segments=[agent("Good morning.")],
        calls=[],
        call_end=4.0,
    )
    assert "unproven" not in finding.detail
