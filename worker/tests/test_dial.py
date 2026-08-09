"""`dial_verdict`: the rule that decides whether an outbound call connected.

This module deliberately imports no livekit, so it runs in CI (which installs
only the dev dependency group). `test_dial_answer.py` covers the same rule
wired to a real room and is skipped without the full stack — this is the
version that guards the regression on every push.
"""

import pytest

from arhiteq_worker.dial import ANSWERED_STATUSES, dial_verdict


class TestUndecided:
    def test_not_dialed_yet_is_not_an_answer(self):
        """The regression: livekit-sip publishes the leg before the INVITE.

        `None` used to read as answered, so a carrier rejection finalized as
        `ended` / `user_hangup` with a ~1s duration instead of
        `not_connected` / `dial_no_answer`.
        """
        assert dial_verdict(None, present=True) is None

    @pytest.mark.parametrize("status", ["dialing", "ringing"])
    def test_in_progress_keeps_waiting(self, status):
        assert dial_verdict(status, present=True) is None

    def test_unknown_status_keeps_waiting(self):
        """A status livekit-sip grows later must not be guessed either way."""
        assert dial_verdict("proceeding", present=True) is None


class TestAnswered:
    @pytest.mark.parametrize("status", sorted(ANSWERED_STATUSES))
    def test_answered_statuses(self, status):
        assert dial_verdict(status, present=True) is True

    def test_answered_wins_over_a_leg_already_gone(self):
        """Answer then hang up still connected — duration must not be zeroed."""
        assert dial_verdict("active", present=False) is True


class TestGaveUp:
    def test_hangup_is_terminal(self):
        assert dial_verdict("hangup", present=True) is False

    def test_absent_leg_is_terminal(self):
        """Rejected before we looked: decide now, don't burn the dial timeout."""
        assert dial_verdict(None, present=False) is False

    def test_absent_while_ringing_is_terminal(self):
        assert dial_verdict("ringing", present=False) is False
