"""`_settle_realtime_session`: the wait that keeps an agent_swap inaudible.

Changing an agent mid-call changes the tool set, and a Gemini Live socket
carries its tools in the setup message — so livekit's google plugin answers
`update_tools` by tearing the connection down and dialling a new one. A swap
that returns before that lands has its reply begin on the dying session, get
clipped when the socket closes, and be generated again in full on the new one.
Requires the livekit stack (main.py imports it at module scope).
"""

import asyncio

import pytest

pytest.importorskip("livekit.agents")

from arhiteq_worker.main import _settle_realtime_session


def _run(coro):
    return asyncio.run(coro)


class _FakeRealtime:
    """Stands in for the plugin's RealtimeSession restart bookkeeping."""

    def __init__(self, *, restarting: bool) -> None:
        self._session_should_close = asyncio.Event()
        if restarting:
            self._session_should_close.set()
        self._active_session = None if restarting else object()


class _FakeActivity:
    def __init__(self, rt) -> None:
        self.realtime_llm_session = rt


class _FakeSession:
    def __init__(self, rt) -> None:
        self._activity = _FakeActivity(rt) if rt is not None else None


def test_pipeline_session_does_not_wait():
    """No realtime session at all (Cartesia pipeline): nothing ever restarts."""
    _run(asyncio.wait_for(_settle_realtime_session(_FakeSession(None), timeout=5.0), 0.5))


def test_settled_live_session_returns_immediately():
    """A Live session with nothing invalidated must not cost the caller a pause."""
    session = _FakeSession(_FakeRealtime(restarting=False))
    _run(asyncio.wait_for(_settle_realtime_session(session, timeout=5.0), 0.5))


def test_waits_for_the_reconnect_to_publish_a_new_socket():
    rt = _FakeRealtime(restarting=True)

    async def scenario():
        settled = asyncio.create_task(_settle_realtime_session(_FakeSession(rt), timeout=5.0))
        await asyncio.sleep(0.05)
        assert not settled.done(), "returned while the socket was still down"

        # The plugin clears the flag at the top of its reconnect loop and only
        # publishes the session once connected — the gap between the two is
        # exactly what a swap must not return into.
        rt._session_should_close.clear()
        await asyncio.sleep(0.05)
        assert not settled.done(), "returned before the new session was published"

        rt._active_session = object()
        await asyncio.wait_for(settled, 1.0)

    _run(scenario())


def test_a_wedged_reconnect_does_not_fail_the_swap(caplog):
    """Timing out is a doubled reply; raising would be a dropped call."""
    session = _FakeSession(_FakeRealtime(restarting=True))
    _run(asyncio.wait_for(_settle_realtime_session(session, timeout=0.1), 2.0))
    assert "did not settle" in caplog.text


def test_a_renamed_plugin_internal_costs_the_wait_not_the_call():
    """The restart flag is private; losing it must degrade, not explode."""

    class _Renamed:
        pass

    _run(asyncio.wait_for(_settle_realtime_session(_FakeSession(_Renamed()), timeout=5.0), 0.5))
