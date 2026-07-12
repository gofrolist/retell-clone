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

    async def press_digit(self, digits: str) -> None:
        pass

    async def agent_swap(self, agent_id: str, entry: dict) -> str:
        return "ok"


def test_all_tool_handlers_resolve_type_hints() -> None:
    from livekit.agents import RunContext

    tools = build_tools(
        [
            {"type": "end_call", "name": "end_call"},
            {"type": "transfer_call", "name": "transfer_call", "number": "+15550001234"},
            {"name": "log_outcome", "url": "https://consumer.example.com/f/log"},
            {"type": "press_digit", "name": "press_digit"},
            {
                "type": "extract_dynamic_variable",
                "name": "extract_vars",
                "variables": [{"name": "plan", "type": "string", "description": "plan"}],
            },
            {
                "type": "check_availability_cal",
                "name": "check_availability",
                "cal_api_key": "cal_k",
                "event_type_id": 1,
            },
            {
                "type": "book_appointment_cal",
                "name": "book_appointment",
                "cal_api_key": "cal_k",
                "event_type_id": 1,
            },
            {
                "type": "send_sms",
                "name": "send_sms",
                "sms_content": {"type": "inferred", "prompt": "confirm the booking"},
            },
            {"type": "agent_swap", "name": "agent_swap", "agent_id": "agent_x"},
        ],
        http=httpx.AsyncClient(),
        function_secret="s",
        variables={},
        control=_Control(),
        state=CallState(call_id="call_x"),
    )
    assert len(tools) == 9
    for tool in tools:
        fnc = getattr(tool, "_fnc", None) or getattr(tool, "fnc", tool)
        hints = typing.get_type_hints(fnc)  # raised NameError before the fix
        assert hints.get("context") is RunContext
