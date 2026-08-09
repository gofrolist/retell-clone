"""`_wait_for_answer`: when an outbound dial counts as answered.

The regression under test: livekit-sip publishes the SIP participant to the
room *before* it sends the INVITE, so for a few hundred milliseconds the
participant carries no ``sip.callStatus`` at all. Reading that gap as
"answered" made a dial Telnyx rejected finalize as ``ended`` / ``user_hangup``
with a ~1s duration instead of ``not_connected`` / ``dial_no_answer``. Seen in
production 2026-07-24..08-08, where every outbound INVITE came back 403
Forbidden and roughly a third were reported to the consumer as answered calls.
Requires the livekit stack (main.py imports it at module scope).
"""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("livekit.agents")

from livekit import rtc

from arhiteq_worker.main import _wait_for_answer


def _run(coro):
    return asyncio.run(coro)


class _Room:
    """Enough rtc.Room for the two events `_wait_for_answer` listens on."""

    def __init__(self, participants):
        self.remote_participants = {p.identity: p for p in participants}
        self._handlers: dict[str, list] = {}

    def on(self, event, cb):
        self._handlers.setdefault(event, []).append(cb)

    def off(self, event, cb):
        self._handlers.get(event, []).remove(cb)

    def _emit(self, event, *args):
        for cb in list(self._handlers.get(event, [])):
            cb(*args)

    def set_status(self, participant, status):
        participant.attributes["sip.callStatus"] = status
        self._emit("participant_attributes_changed", {"sip.callStatus": status}, participant)

    def disconnect(self, participant):
        self.remote_participants.pop(participant.identity, None)
        self._emit("participant_disconnected", participant)


def _sip_participant(status=None):
    attributes = {} if status is None else {"sip.callStatus": status}
    return SimpleNamespace(
        identity="pstn_+15550001111",
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        attributes=attributes,
    )


def _answer(room, participant, timeout=5.0):
    return _wait_for_answer(SimpleNamespace(room=room), participant, timeout=timeout)


async def _answer_after(room, participant, act):
    waiter = asyncio.ensure_future(_answer(room, participant))
    await asyncio.sleep(0)
    act()
    return await waiter


class TestSipDial:
    def test_pending_status_is_not_an_answer(self):
        """The bug: no sip.callStatus yet, then Telnyx rejects the INVITE."""
        p = _sip_participant()
        room = _Room([p])
        assert _run(_answer_after(room, p, lambda: room.disconnect(p))) is False

    def test_already_gone_returns_without_burning_the_dial_timeout(self):
        p = _sip_participant()
        room = _Room([])  # disconnected before the worker looked
        assert _run(asyncio.wait_for(_answer(room, p, timeout=30.0), 1.0)) is False

    def test_already_hung_up_returns_without_burning_the_dial_timeout(self):
        """Still in the room, but the leg is over — the event stream is silent."""
        p = _sip_participant("hangup")
        room = _Room([p])
        assert _run(asyncio.wait_for(_answer(room, p, timeout=30.0), 1.0)) is False

    def test_ringing_then_active_is_an_answer(self):
        p = _sip_participant("ringing")
        room = _Room([p])
        assert _run(_answer_after(room, p, lambda: room.set_status(p, "active"))) is True

    def test_already_active_is_an_answer(self):
        p = _sip_participant("active")
        assert _run(_answer(_Room([p]), p)) is True

    def test_hangup_while_ringing_is_not_an_answer(self):
        p = _sip_participant("ringing")
        room = _Room([p])
        assert _run(_answer_after(room, p, lambda: room.set_status(p, "hangup"))) is False

    def test_unanswered_ring_times_out(self):
        p = _sip_participant("ringing")
        assert _run(_answer(_Room([p]), p, timeout=0.05)) is False


class TestWebCall:
    def test_browser_caller_is_answered_on_sight(self):
        """No SIP leg to ring — the browser is already in the room."""
        p = SimpleNamespace(
            identity="web_call_x",
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
            attributes={},
        )
        assert _run(_answer(_Room([p]), p)) is True
