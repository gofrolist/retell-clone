"""The audio rules, tested without any audio.

Every case here is written from a bug that actually shipped or a false positive
that would make the rule unusable. Stdlib only, so this runs in the worker's
existing dev-group CI job.
"""

from __future__ import annotations

from audio.analysis import (
    AGENT,
    CALLER,
    Finding,
    Segment,
    analyse,
    duplicate_utterances,
    format_findings,
    long_silences,
    normalise,
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
    assert long_silences(segments) == []


def test_a_gap_over_the_limit_is_reported_with_where_to_listen():
    segments = [caller(0.0, 2.0, "How much is it?"), agent(9.0, 10.0, "Sorry.")]
    findings = long_silences(segments)
    assert len(findings) == 1
    assert findings[0].rule == "max_silence"
    assert findings[0].at == 2.0


def test_the_limit_is_exclusive_so_a_gap_exactly_at_it_passes():
    segments = [caller(0.0, 2.0, "Hello?"), agent(6.0, 7.0, "Hello.")]
    assert long_silences(segments, limit_s=4.0) == []


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


def test_format_findings_says_where_to_listen():
    text = format_findings([Finding(rule="max_silence", detail="7.0s of silence", at=12.5)])
    assert "12.50s" in text
    assert "max_silence" in text
