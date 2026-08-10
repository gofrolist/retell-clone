"""The exit-code contract, which is the only thing a scheduler reads."""

from __future__ import annotations

from audio.verdict import (
    BROKEN,
    CLEAN,
    FINDINGS,
    describe,
    exit_code,
    room_close_ended_the_call,
)


def test_a_clean_call_is_clean():
    assert exit_code(stopped="script_finished", segments=6, findings=0) == CLEAN


def test_findings_on_a_finished_call_are_a_prompt_verdict():
    assert exit_code(stopped="script_finished", segments=6, findings=2) == FINDINGS


def test_nobody_joining_is_broken_not_clean():
    # No worker, no audio, no findings — and reported as a passing call unless
    # this is spelled out. Asserted with segments present so the reason itself
    # is what makes it broken: in practice nobody joining also means nothing
    # was recorded, and a test that leans on that coincidence stops pinning the
    # reason at all.
    assert exit_code(stopped="agent_never_joined", segments=4, findings=0) == BROKEN


def test_a_call_cut_off_by_its_own_time_limit_is_broken():
    # The call happened and produced findings, but the script never finished,
    # so the findings cover half the case — and the half that was never spoken
    # is silently scored as fine.
    assert exit_code(stopped="max_call_s", segments=8, findings=1) == BROKEN


def test_the_agent_hanging_up_is_a_verdict_not_a_broken_run():
    # It shares a symptom with `max_call_s` — an unfinished script — and not a
    # cause. An agent that hangs up before saying goodbye is the bug a case is
    # looking for, and grading that run as "broken" throws the finding away.
    assert exit_code(stopped="agent_ended_call", segments=8, findings=1) == FINDINGS
    assert exit_code(stopped="agent_ended_call", segments=8, findings=0) == CLEAN


def test_a_crash_mid_call_is_broken_even_with_findings():
    assert exit_code(stopped="error", segments=4, findings=3) == BROKEN


def test_a_recording_with_nothing_in_it_is_broken_however_it_ended():
    # "No findings" over an empty recording is not an observation about the
    # prompt; it is the absence of any observation at all.
    assert exit_code(stopped="script_finished", segments=0, findings=0) == BROKEN


def test_every_code_can_be_explained_to_whoever_reads_the_log():
    assert all(describe(code) for code in (CLEAN, FINDINGS, BROKEN))


# --- which room-level disconnects were the agent ------------------------
#
# `caller.py` cannot be tested here (it imports livekit), which is exactly why
# the decision it makes about a disconnect lives in this module.


def test_the_worker_deleting_the_room_is_the_agent_hanging_up():
    # `RuntimeControl.end_call` ends a call with `delete_room`, so this is what
    # a hangup looks like from the caller's side.
    assert room_close_ended_the_call("ROOM_DELETED")
    assert room_close_ended_the_call("ROOM_CLOSED")


def test_the_harness_losing_its_connection_is_not_a_hangup():
    # The failure this guards: any disconnect counted as a hangup gives
    # `agent_ended_call`, which is deliberately not a broken reason, so a
    # truncated run exits CLEAN and a harness failure reads as a passing call.
    for reason in ("CLIENT_INITIATED", "SIGNAL_CLOSE", "SERVER_SHUTDOWN", "JOIN_FAILURE"):
        assert not room_close_ended_the_call(reason)
        assert exit_code(stopped="room_closed", segments=8, findings=0) == BROKEN


def test_an_unrecognised_reason_is_not_a_hangup():
    # A new DisconnectReason in a later livekit-rtc is far more likely to be a
    # new way for a connection to fail than a new way to say goodbye.
    assert not room_close_ended_the_call("SOMETHING_ADDED_IN_2027")
    assert not room_close_ended_the_call("")
