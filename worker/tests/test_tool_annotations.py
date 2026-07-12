"""Tool handlers must have runtime-resolvable type hints.

livekit-agents calls typing.get_type_hints() on every function tool at
EXECUTION time to find the RunContext parameter. With stringized
annotations (`from __future__ import annotations` in tools.py) that
lookup raised NameError('RunContext') and every built-in tool crashed
mid-call. Requires the livekit stack — skipped in the dev-only test env,
exercised in the full (image) environment.
"""

import typing

import httpx
import pytest

pytest.importorskip("livekit.agents")

from architeq_worker.state import CallState
from architeq_worker.tools import build_tools


class _Control:
    async def end_call(self, reason: str = "agent_hangup") -> None:
        pass

    async def transfer_call(self, number: str) -> str:
        return "ok"


def test_all_tool_handlers_resolve_type_hints() -> None:
    from livekit.agents import RunContext

    tools = build_tools(
        [
            {"type": "end_call", "name": "end_call"},
            {"type": "transfer_call", "name": "transfer_call", "number": "+15550001234"},
            {"name": "log_outcome", "url": "https://consumer.example.com/f/log"},
        ],
        http=httpx.AsyncClient(),
        function_secret="s",
        variables={},
        control=_Control(),
        state=CallState(call_id="call_x"),
    )
    assert len(tools) == 3
    for tool in tools:
        fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
        hints = typing.get_type_hints(fnc)  # raised NameError before the fix
        assert hints.get("context") is RunContext
