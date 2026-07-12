"""Tool-bridge execution contract tests (docs/ARCHITECTURE.md rule 4):
flat args body, X-Caller-Secret header, {{var}} resolution, JSON result,
{"error": ...} on failure. Uses httpx.MockTransport — no network.
"""

import asyncio
import json

import httpx

from architeq_worker.state import CallState
from architeq_worker.tools import execute_custom_tool, safe_execute_custom_tool


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_body_is_flat_args_with_secret_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["secret"] = request.headers.get("X-Caller-Secret")
        return httpx.Response(200, json={"ok": True})

    async def run() -> str:
        async with _client(handler) as http:
            return await execute_custom_tool(
                http,
                url="https://consumer.example.com/functions/v1/log-outcome",
                args={"outcome": "answered", "count": 2, "flag": True},
                function_secret="sekret",
                variables={},
            )

    result = asyncio.run(run())
    # CONTRACT: flat body — never {"args": {...}}.
    assert captured["body"] == {"outcome": "answered", "count": 2, "flag": True}
    assert "args" not in captured["body"]
    assert captured["secret"] == "sekret"
    assert json.loads(result) == {"ok": True}


def test_dynamic_variables_resolved_in_string_args() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http,
                url="https://consumer.example.com/tool",
                args={
                    "name": "{{first_name}}",
                    "note": "call {{first_name}} at {{phone}}",
                    "missing": "{{unknown}}",
                    "nested": {"inner": "{{phone}}"},
                    "n": 1,
                },
                function_secret="s",
                variables={"first_name": "John", "phone": "+15551234567"},
            )

    asyncio.run(run())
    assert captured["body"] == {
        "name": "John",
        "note": "call John at +15551234567",
        "missing": "{{unknown}}",  # unknown vars stay literal
        "nested": {"inner": "+15551234567"},
        "n": 1,
    }


def test_call_object_sent_alongside_flat_args() -> None:
    """Retell POSTs a `call` object with custom-function args; consumer
    handlers fall back to call.call_id / call.from_number /
    call.retell_llm_dynamic_variables.phone when args omit them."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    call_info = {
        "call_id": "call_abc123",
        "direction": "outbound",
        "from_number": "+19499195585",
        "to_number": "+15551234567",
        "retell_llm_dynamic_variables": {"phone": "+15551234567"},
        "metadata": {},
    }

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http,
                url="https://consumer.example.com/functions/v1/end-call",
                args={"outcome": "answered"},
                function_secret="s",
                variables={},
                call_info=call_info,
            )

    asyncio.run(run())
    # args stay flat at the top level; `call` rides alongside them.
    assert captured["body"]["outcome"] == "answered"
    assert "args" not in captured["body"]
    assert captured["body"]["call"] == call_info


def test_call_scoped_template_resolves_in_args() -> None:
    """log_outcome specs say: pass the exact value of {{call.call_id}}."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http,
                url="https://consumer.example.com/functions/v1/log-outcome",
                args={"retell_call_id": "{{call.call_id}}", "phone": "{{phone}}"},
                function_secret="s",
                variables={"call.call_id": "call_abc123", "phone": "+15551234567"},
            )

    asyncio.run(run())
    assert captured["body"]["retell_call_id"] == "call_abc123"
    assert captured["body"]["phone"] == "+15551234567"


def test_error_response_returns_error_json_to_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def run() -> str:
        async with _client(handler) as http:
            return await safe_execute_custom_tool(
                http,
                name="log_outcome",
                url="https://consumer.example.com/tool",
                args={"a": "b"},
                function_secret="s",
                variables={},
            )

    result = json.loads(asyncio.run(run()))
    assert "error" in result


def test_timeout_returns_error_json_to_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    async def run() -> str:
        async with _client(handler) as http:
            return await safe_execute_custom_tool(
                http,
                name="slow_tool",
                url="https://consumer.example.com/slow",
                args={},
                function_secret="s",
                variables={},
            )

    result = json.loads(asyncio.run(run()))
    assert "error" in result


def test_tool_calls_recorded_in_call_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True})

    state = CallState()

    async def run() -> None:
        async with _client(handler) as http:
            await safe_execute_custom_tool(
                http,
                name="set_reminder",
                url="https://consumer.example.com/tool",
                args={"when": "9am"},
                function_secret="s",
                variables={},
                state=state,
            )

    asyncio.run(run())
    roles = [item["role"] for item in state.items]
    assert roles == ["tool_call_invocation", "tool_call_result"]
    assert state.items[0]["name"] == "set_reminder"
    assert json.loads(state.items[1]["content"]) == {"done": True}
    # tool records appear in transcript_with_tool_calls but not transcript.
    assert state.transcript_object() == []
