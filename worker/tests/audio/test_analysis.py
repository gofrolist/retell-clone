"""The audio rules, tested without any audio.

Every case here is written from a bug that actually shipped or a false positive
that would make the rule unusable. Stdlib only, so this runs in the worker's
existing dev-group CI job.
"""

from __future__ import annotations

import pytest

from audio.analysis import (
    AGENT,
    CALLER,
    DEFAULT_MAX_SILENCE_S,
    DEFAULT_SIMILARITY,
    Finding,
    Segment,
    analyse,
    duplicate_utterances,
    format_findings,
    long_silences,
    normalise,
    repeats_itself,
    restarted_turns,
    silences,
    similarity,
)

GREETING = "Good morning Margaret, it's Clara. How are you feeling today?"


def agent(start: float, end: float, text: str) -> Segment:
    return Segment(start=start, end=end, text=text, speaker=AGENT)


def caller(start: float, end: float, text: str) -> Segment:
    return Segment(start=start, end=end, text=text, speaker=CALLER)


# --- normalisation and similarity ---------------------------------------


def test_normalise_ignores_what_asr_guesses_at():
    # Case and punctuation are the transcriber's choices, not the agent's
    # behaviour. Treating them as differences would hide a real duplicate.
    assert normalise("Good morning, Margaret!") == normalise("good morning margaret")


def test_normalise_collapses_whitespace():
    assert normalise("  hello   there \n") == "hello there"


def test_similarity_is_one_for_the_same_line_punctuated_differently():
    assert similarity("It's Clara.", "its clara") == 1.0


def test_similarity_separates_different_sentences():
    assert similarity(GREETING, "Did you take your Lipitor this morning?") < 0.5


# --- no_duplicate_utterance ---------------------------------------------


def test_the_double_greeting_is_caught():
    # PR #224: the begin_message spoken twice on a Gemini Live call. This is the
    # bug the whole audio layer exists for -- it is invisible in a transcript.
    findings = duplicate_utterances([agent(0.0, 3.2, GREETING), agent(3.4, 6.6, GREETING)])
    assert len(findings) == 1
    assert findings[0].rule == "no_duplicate_utterance"
    assert findings[0].at == 3.4


def test_a_legitimate_repeat_much_later_is_not_a_duplicate():
    # Asking again after a digression is the prompt working, not a bug.
    findings = duplicate_utterances([agent(0.0, 3.2, GREETING), agent(90.0, 93.2, GREETING)])
    assert findings == []


def test_short_backchannels_are_never_duplicates():
    # Without the minimum-length guard this rule is a false-positive machine:
    # "Okay." and "Okay." are 100% alike and a caller hears nothing wrong.
    findings = duplicate_utterances(
        [agent(0.0, 0.4, "Okay."), agent(1.0, 1.4, "Okay."), agent(2.0, 2.4, "Mm-hm.")]
    )
    assert findings == []


def test_the_callers_own_repeated_lines_are_not_the_agents_fault():
    # A case may deliberately repeat a caller line to see what the agent does.
    # The harness is the one speaking those, so they can never be a finding.
    findings = duplicate_utterances(
        [
            caller(0.0, 2.0, "I haven't taken the Lipitor yet."),
            caller(3.0, 5.0, "I haven't taken the Lipitor yet."),
        ]
    )
    assert findings == []


def test_near_identical_counts_as_duplicate():
    # ASR of the same synthesized line twice is rarely character-identical, so
    # an exact-match rule would miss every real occurrence.
    findings = duplicate_utterances(
        [
            agent(0.0, 3.0, "Good morning Margaret, it's Clara."),
            agent(3.2, 6.2, "Good morning Margaret its Clara"),
        ]
    )
    assert len(findings) == 1


def test_segments_out_of_time_order_still_find_the_duplicate():
    # Segments are merged from two per-track event logs, so nothing guarantees
    # they arrive sorted. Unsorted, the scan breaks out of its window on the
    # late segment and never sees the duplicate right after it — losing exactly
    # the greeting-twice bug the rule exists for.
    findings = duplicate_utterances(
        [
            agent(0.0, 3.0, GREETING),
            agent(100.0, 103.0, "Right, let me get that sorted out for you now."),
            agent(3.2, 6.2, GREETING),
        ]
    )
    assert len(findings) == 1


def test_a_stuck_agent_is_not_reported_once_per_pair():
    # Reporting every pair gives N*(N-1)/2 findings — six for four repeats — so
    # one looping bug fills the whole report. Three greetings are two repeats.
    findings = duplicate_utterances(
        [agent(0.0, 3.0, GREETING), agent(3.2, 6.2, GREETING), agent(6.4, 9.4, GREETING)]
    )
    assert len(findings) == 2


def test_a_medication_run_through_is_not_a_duplicate():
    # Clara reads a medication list, so her own script contains near-identical
    # sentences differing only by the drug. This is the pair most likely to be
    # wrongly flagged once the threshold gets tuned down during calibration.
    assert (
        similarity(
            "Did you take your Lipitor this morning?",
            "Did you take your Metformin this morning?",
        )
        < 0.9
    )
    findings = duplicate_utterances(
        [
            agent(0.0, 3.0, "Did you take your Lipitor this morning?"),
            agent(8.0, 11.0, "Did you take your Metformin this morning?"),
        ]
    )
    assert findings == []


def test_the_window_is_measured_between_utterances_not_from_their_starts():
    # A long utterance whose START is inside the window but which ended well
    # before it is not "in quick succession". Measuring start-to-start would
    # make the window depend on how long the agent talks for.
    long_line = GREETING + " " + " ".join(["and so on"] * 40)
    findings = duplicate_utterances(
        [agent(0.0, 40.0, long_line), agent(41.0, 81.0, long_line)],
        window_s=30.0,
    )
    assert len(findings) == 1, "1s apart, however long each one took to say"


def test_a_double_said_inside_one_utterance_is_caught():
    # Verbatim from a control call: the same agent doubled its greeting AND its
    # reply. The greeting came back as two segments and was caught; the reply
    # had a shorter pause between the copies, arrived as one segment, and a rule
    # that only compares segments with each other saw nothing wrong with it.
    doubled = (
        "I am glad to hear that you are doing well. I am glad to hear that you are doing well."
    )
    findings = duplicate_utterances([agent(13.9, 19.0, doubled)])
    assert len(findings) == 1
    assert "within one utterance" in findings[0].detail
    assert findings[0].at == 13.9


def test_a_double_whose_second_copy_is_longer_is_still_a_double():
    # ASR adds a trailing word and a midpoint split stops aligning. Anchored
    # only at the outer ends this scores 0.875 and slips under the bar — the
    # same bug the rule was added for, defeated by one word.
    assert repeats_itself(
        "I'm glad to hear that you're doing well. I'm glad to hear that you're doing well today."
    )


def test_a_double_whose_second_copy_is_shorter_is_still_a_double():
    assert repeats_itself(
        "Is there anything else I can help you with. Is there anything else I can help with."
    )


def test_a_medication_run_through_in_one_breath_is_not_a_double():
    # Clara reads a list, and two consecutive questions differing only by the
    # drug are exactly what the halves of one segment look like. This is the
    # false positive that would make the rule unusable on the real prompt.
    assert not repeats_itself("Did you take your Lipitor? Did you take your Metformin?")


def test_short_emphatic_doubling_is_not_a_double():
    # "Yes, yes." and "Of course, of course." are speech, not a bug.
    assert not repeats_itself("Yes, yes.")
    assert not repeats_itself("Of course, of course.")


def test_an_ordinary_sentence_does_not_repeat_itself():
    assert not repeats_itself(GREETING)
    assert not repeats_itself("Let me pull up the pricing on that for you right now.")


def test_a_doubled_utterance_is_reported_once_not_also_as_a_pairwise_repeat():
    # Two segments that each say the same thing twice are two bugs, not four.
    doubled = "Is there anything else I can help with. Is there anything else I can help with."
    findings = duplicate_utterances([agent(0.0, 4.0, doubled), agent(4.5, 8.5, doubled)])
    assert len(findings) == 2
    assert all("within one utterance" in f.detail for f in findings)


# --- max_silence ---------------------------------------------------------


def test_silence_is_measured_from_the_caller_stopping_to_the_agent_starting():
    segments = [caller(0.0, 2.0, "Morning Clara."), agent(7.5, 9.0, "Good morning.")]
    assert silences(segments) == [(2.0, 7.5)]


def test_silence_after_the_agent_stops_is_not_the_agents_fault():
    # That gap is the caller thinking. Counting it would fail every case whose
    # script has a pause in it.
    segments = [agent(0.0, 3.0, GREETING), caller(20.0, 22.0, "Morning.")]
    assert silences(segments) == []


def test_a_caller_turn_the_agent_never_answers_is_the_worst_silence_not_none():
    # The agent freezing entirely must not read as a clean call because there is
    # no following segment to measure to.
    segments = [caller(0.0, 2.0, "Morning Clara.")]
    assert silences(segments, call_end=30.0) == [(2.0, 30.0)]
    findings = long_silences(segments, call_end=30.0)
    assert len(findings) == 1
    assert "28.0s" in findings[0].detail


def test_a_gap_under_the_limit_is_not_reported():
    # The ~850ms an agent_swap costs to rebuild its socket is known and fine.
    segments = [caller(0.0, 2.0, "How much is it?"), agent(2.9, 4.0, "Let me pull that up.")]
    assert long_silences(segments, call_end=20.0) == []


def test_a_gap_over_the_limit_is_reported_with_where_to_listen():
    segments = [caller(0.0, 2.0, "How much is it?"), agent(9.0, 10.0, "Sorry.")]
    findings = long_silences(segments, call_end=20.0)
    assert len(findings) == 1
    assert findings[0].rule == "max_silence"
    assert findings[0].at == 2.0


def test_the_limit_is_exclusive_so_a_gap_exactly_at_it_passes():
    segments = [caller(0.0, 2.0, "Hello?"), agent(6.0, 7.0, "Hello.")]
    assert long_silences(segments, call_end=20.0, limit_s=4.0) == []


def test_a_caller_turn_split_into_segments_is_one_turn():
    # VAD and ASR routinely chop one caller turn into several segments. Measuring
    # from each of them counts the caller's OWN later speech as dead air: this
    # call would report 8.4s of silence when the caller talked for 7.7 of it.
    # Every multi-segment turn on a real call becomes a false positive, which is
    # fatal for a rule whose whole job is to be believed.
    segments = [
        caller(0.0, 2.0, "Morning Clara."),
        caller(2.3, 10.0, "and I slept badly, if I'm honest."),
        agent(10.4, 12.0, "I'm sorry to hear that."),
    ]
    assert silences(segments) == [(10.0, 10.4)]
    assert long_silences(segments, call_end=20.0) == []


def test_an_answer_that_overlaps_the_callers_tail_is_still_an_answer():
    # Barge-in and early starts are ordinary on a speech-to-speech model. A
    # strict starts-after-the-caller-finished test misses them and falls through
    # to the never-answered branch, reporting the whole turn as dead air on a
    # call the agent answered before the caller stopped.
    segments = [caller(0.0, 10.0, "It's been quite a week, honestly."), agent(9.6, 13.0, "I bet.")]
    assert silences(segments, call_end=20.0) == [(10.0, 10.0)]
    assert long_silences(segments, call_end=20.0) == []


def test_one_freeze_is_reported_once_not_once_per_trailing_segment():
    segments = [caller(0.0, 2.0, "Hello?"), caller(3.0, 5.0, "Are you there?")]
    findings = long_silences(segments, call_end=30.0)
    assert len(findings) == 1
    assert findings[0].at == 5.0, "measured from the last thing the caller said"


def test_call_end_cannot_be_forgotten():
    # The loudest failure in the layer — the agent going silent and never coming
    # back — needs a boundary to measure to, and has none if the caller's turn is
    # the last segment. Defaulting it does not save this: the obvious default,
    # the end of the last segment, IS the caller's own end in that exact case, so
    # the gap is zero and the freeze still vanishes. Only refusing to run without
    # it works.
    segments = [agent(0.0, 3.0, GREETING), caller(5.0, 7.0, "Hello? Are you still there?")]
    with pytest.raises(TypeError):
        long_silences(segments)
    with pytest.raises(TypeError):
        analyse(segments)
    assert len(long_silences(segments, call_end=40.0)) == 1


# --- the whole pass ------------------------------------------------------


def test_analyse_orders_findings_by_when_they_happened():
    # The report is read beside a recording, so it should walk through it.
    segments = [
        caller(0.0, 1.0, "Morning."),
        agent(12.0, 15.0, GREETING),
        agent(15.2, 18.2, GREETING),
    ]
    findings = analyse(segments, call_end=20.0)
    assert [f.rule for f in findings] == ["max_silence", "no_duplicate_utterance"]
    assert findings[0].at < findings[1].at


def test_a_clean_call_produces_nothing():
    segments = [
        agent(0.0, 3.0, GREETING),
        caller(3.5, 5.0, "I'm well, thank you."),
        agent(5.4, 8.0, "Glad to hear it. Did you take your Lipitor?"),
    ]
    assert analyse(segments, call_end=9.0) == []
    assert format_findings([]) == "No audio findings."


# --- restarted turns -----------------------------------------------------
#
# Every transcript below is copied from the first Live-model run, where the
# model abandoned turns mid-sentence and started them again. The audio rules
# reported a clean call on all of it.


def turn(role, content, time_ms=1000):
    return {"role": role, "content": content, "time_ms": time_ms}


def test_a_fragment_followed_by_the_finished_sentence_is_a_restart():
    turns = [
        turn(AGENT, "That's wonderful to", 8000),
        turn(AGENT, "That's wonderful to hear. Did you sleep alright last night?", 9040),
    ]
    findings = restarted_turns(turns)
    assert [f.rule for f in findings] == ["no_restarted_turn"]
    assert findings[0].at == 9.04


def test_a_restart_that_diverges_after_the_opening_still_counts():
    # The listener hears "Good for you. Is there any... Good for you. Before I
    # let you go" — the second attempt is not a continuation of the first.
    turns = [
        turn(AGENT, "Good for you. Is there anything"),
        turn(
            AGENT, "Good for you. Before I let you go, is there anything else I can help you with?"
        ),
    ]
    assert len(restarted_turns(turns)) == 1


def test_a_caller_turn_in_between_makes_a_repeated_opening_ordinary():
    # An agent re-asking a question it never got an answer to is the prompt
    # working, not a restart.
    turns = [
        turn(AGENT, "Did you take your Lipitor"),
        turn(CALLER, "Sorry, what was that?"),
        turn(AGENT, "Did you take your Lipitor this morning?"),
    ]
    assert restarted_turns(turns) == []


def test_a_finished_sentence_is_never_a_restart():
    # The medication run-through: same four-word opening, but the first turn
    # ENDS. A model that finished its sentence did not abandon it.
    turns = [
        turn(AGENT, "Did you take your Lipitor?"),
        turn(AGENT, "Did you take your Metformin?"),
    ]
    assert restarted_turns(turns) == []


def test_two_unrelated_turns_in_a_row_are_left_alone():
    turns = [
        turn(AGENT, "Good morning Margaret, it's Clara"),
        turn(AGENT, "How are you feeling today?"),
    ]
    assert restarted_turns(turns) == []


def test_restarts_ride_along_with_the_audio_rules():
    segments = [
        agent(0.0, 3.0, GREETING),
        caller(3.5, 5.0, "I'm well."),
        agent(5.4, 9.0, "That's wonderful to hear. That's wonderful to hear. Did you sleep?"),
    ]
    turns = [
        turn(AGENT, "That's wonderful to", 5400),
        turn(AGENT, "That's wonderful to hear. Did you sleep?", 6000),
    ]
    rules = {f.rule for f in analyse(segments, call_end=10.0, turns=turns)}
    # Both halves of the same event: the platform's fragment and the stutter a
    # listener actually heard.
    assert rules == {"no_restarted_turn", "no_duplicate_utterance"}


# --- the doubling that is only the opening -------------------------------


def test_an_utterance_that_repeats_its_opening_then_carries_on_is_caught():
    # Eight words said twice, then seven new ones. The midpoint search cannot
    # see this: the utterance is not two copies of anything, and the seam is a
    # quarter of the way in.
    doubled = (
        "That's wonderful to hear. That's wonderful to hear. Did you sleep all right last night?"
    )
    assert repeats_itself(doubled) >= DEFAULT_SIMILARITY


def test_a_parallel_run_through_is_still_not_a_repeat():
    # Four exact words shared, then a different drug. The prefix scan only
    # accepts an exact repeat, so this stays out.
    assert repeats_itself("Did you take your Lipitor did you take your Metformin") == 0.0


def test_the_silence_limit_clears_every_turnaround_ever_measured():
    # The baseline behind `DEFAULT_MAX_SILENCE_S`, as a test rather than only as
    # a comment: 13 Clara calls produced 35 answered gaps and the longest was
    # 5.11s. A limit at or under that flags ordinary turns — the previous 4.0s
    # flagged 6 of the 35 — and a rule that cries wolf is a rule people mute.
    # If a future measurement genuinely moves this, move it deliberately and
    # bring the numbers with you.
    slowest_observed = 5.11
    assert DEFAULT_MAX_SILENCE_S > slowest_observed
    segments = [caller(0.0, 2.0, "Is it warm enough for a walk?")]
    answered_slowly = segments + [agent(2.0 + slowest_observed, 12.0, "It's seventy-four.")]
    assert long_silences(answered_slowly, call_end=12.0) == []


def test_format_findings_says_where_to_listen():
    text = format_findings([Finding(rule="max_silence", detail="7.0s of silence", at=12.5)])
    assert "12.50s" in text
    assert "max_silence" in text
