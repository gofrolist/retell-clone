"""The exit-code contract, which is the only thing a scheduler reads."""

from __future__ import annotations

from audio.verdict import BROKEN, CLEAN, FINDINGS, describe, exit_code


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


def test_a_crash_mid_call_is_broken_even_with_findings():
    assert exit_code(stopped="error", segments=4, findings=3) == BROKEN


def test_a_recording_with_nothing_in_it_is_broken_however_it_ended():
    # "No findings" over an empty recording is not an observation about the
    # prompt; it is the absence of any observation at all.
    assert exit_code(stopped="script_finished", segments=0, findings=0) == BROKEN


def test_every_code_can_be_explained_to_whoever_reads_the_log():
    assert all(describe(code) for code in (CLEAN, FINDINGS, BROKEN))
