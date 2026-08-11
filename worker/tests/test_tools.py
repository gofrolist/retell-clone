"""Tool-bridge execution contract tests (docs/ARCHITECTURE.md rule 4):
flat args body, X-Caller-Secret header, {{var}} resolution, JSON result,
{"error": ...} on failure. Uses httpx.MockTransport — no network.
"""

import asyncio
import json

import httpx
import pytest

from arhiteq_worker.state import CallState
from arhiteq_worker.tools import (
    MAX_TOOL_RESPONSE_BYTES,
    UnsafeToolUrlError,
    assert_tool_url_safe,
    execute_custom_tool,
    safe_execute_custom_tool,
)


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


def test_ssrf_guard_blocks_private_address(monkeypatch) -> None:
    # Without the dev bypass, a tool URL resolving to a private/link-local
    # address (e.g. the metadata server) must be rejected before any request.
    monkeypatch.delenv("ARHITEQ_ALLOW_PRIVATE_WEBHOOKS", raising=False)

    async def run() -> None:
        for url in (
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:9090/metrics",
            "http://[::1]/",
            "ftp://consumer.example.com/x",
        ):
            with pytest.raises(UnsafeToolUrlError):
                await assert_tool_url_safe(url)

    asyncio.run(run())


def test_ssrf_guard_surfaces_as_tool_error(monkeypatch) -> None:
    monkeypatch.delenv("ARHITEQ_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    called = {"posted": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["posted"] = True
        return httpx.Response(200, json={})

    async def run() -> str:
        async with _client(handler) as http:
            return await safe_execute_custom_tool(
                http,
                name="evil",
                url="http://169.254.169.254/",
                args={},
                function_secret="s",
                variables={},
            )

    result = json.loads(asyncio.run(run()))
    assert "error" in result
    assert called["posted"] is False  # blocked before the POST


def test_oversized_response_becomes_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_TOOL_RESPONSE_BYTES + 1))

    async def run() -> str:
        async with _client(handler) as http:
            return await safe_execute_custom_tool(
                http,
                name="huge",
                url="https://consumer.example.com/tool",
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


def _counting_client(calls: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "seq": len(calls)})

    return _client(handler)


def test_repeated_identical_tool_call_is_not_sent_twice() -> None:
    # Issue #250: a Gemini Live turn abandoned after its tool ran loses the
    # result before the model sees it, so the model re-issues the identical
    # call. The endpoint must be written once — a second POST here is a second
    # dose in a member's medication record.
    calls: list[httpx.Request] = []
    state = CallState()

    async def run() -> tuple[str, str]:
        async with _counting_client(calls) as http:
            kwargs = {
                "name": "log_medication_taken",
                "url": "https://consumer.example.com/log-medication",
                "function_secret": "s",
                "variables": {},
                "state": state,
            }
            first = await safe_execute_custom_tool(
                http, args={"taken": True, "phone": "+1555", "medication_name": "Lipitor"}, **kwargs
            )
            # Key order differs on the retry, exactly as the model re-emits it.
            second = await safe_execute_custom_tool(
                http, args={"medication_name": "Lipitor", "phone": "+1555", "taken": True}, **kwargs
            )
            return first, second

    first, second = asyncio.run(run())
    assert len(calls) == 1
    assert first == second
    # Both invocations stay in the transcript: the model did call twice, and
    # the platform record has to keep saying so.
    roles = [item["role"] for item in state.items]
    assert roles.count("tool_call_invocation") == 2
    assert roles.count("tool_call_result") == 2


def test_replay_expires_with_the_window(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    state = CallState()
    monkeypatch.setattr("arhiteq_worker.tools.TOOL_REPLAY_WINDOW_S", 0.0)

    async def run() -> None:
        async with _counting_client(calls) as http:
            for _ in range(2):
                await safe_execute_custom_tool(
                    http,
                    name="log_mood",
                    url="https://consumer.example.com/log-mood",
                    args={"mood": "ok"},
                    function_secret="s",
                    variables={},
                    state=state,
                )

    asyncio.run(run())
    assert len(calls) == 2


def test_differing_arguments_are_not_deduplicated() -> None:
    calls: list[httpx.Request] = []
    state = CallState()

    async def run() -> None:
        async with _counting_client(calls) as http:
            for mood in ("doing alright", "slept well"):
                await safe_execute_custom_tool(
                    http,
                    name="log_mood",
                    url="https://consumer.example.com/log-mood",
                    args={"mood_summary": mood},
                    function_secret="s",
                    variables={},
                    state=state,
                )

    asyncio.run(run())
    assert len(calls) == 2


def test_a_failed_call_is_retried_rather_than_replayed() -> None:
    # A retry after a failure is the model recovering, not the #250 duplicate:
    # it has to reach the endpoint.
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})

    state = CallState()

    async def run() -> str:
        async with _client(handler) as http:
            for _ in range(2):
                result = await safe_execute_custom_tool(
                    http,
                    name="log_outcome",
                    url="https://consumer.example.com/log-outcome",
                    args={"outcome": "done"},
                    function_secret="s",
                    variables={},
                    state=state,
                )
            return result

    result = asyncio.run(run())
    assert len(attempts) == 2
    assert json.loads(result) == {"ok": True}


def test_replay_cache_does_not_cross_calls() -> None:
    calls: list[httpx.Request] = []

    async def run() -> None:
        async with _counting_client(calls) as http:
            for _ in range(2):
                await safe_execute_custom_tool(
                    http,
                    name="log_mood",
                    url="https://consumer.example.com/log-mood",
                    args={"mood": "ok"},
                    function_secret="s",
                    variables={},
                    state=CallState(),
                )

    asyncio.run(run())
    assert len(calls) == 2


def test_a_retry_arriving_mid_request_does_not_double_write() -> None:
    # The claim has to be staked before the request, not after it: livekit runs
    # each function call of a generation as its own task, and the doubled
    # generation can re-emit the call while the first is still in flight.
    # Claiming only on completion would let both copies reach the endpoint.
    calls: list[httpx.Request] = []
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        started.set()
        await asyncio.sleep(0.05)  # the endpoint is slower than the retry gap
        return httpx.Response(200, json={"ok": True})

    state = CallState()

    async def run() -> list[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

            async def once() -> str:
                return await safe_execute_custom_tool(
                    http,
                    name="log_medication_taken",
                    url="https://consumer.example.com/log-medication",
                    args={"taken": True, "medication_name": "Lipitor"},
                    function_secret="s",
                    variables={},
                    state=state,
                )

            first = asyncio.create_task(once())
            await started.wait()  # retry lands mid-flight
            second = asyncio.create_task(once())
            return list(await asyncio.gather(first, second))

    first, second = asyncio.run(run())
    assert len(calls) == 1
    assert first == second


def test_read_tools_are_not_replayed() -> None:
    # A GET has no write to prevent, and a cached read would have the agent
    # restate stale data as freshly fetched.
    calls: list[httpx.Request] = []
    state = CallState()

    async def run() -> None:
        async with _counting_client(calls) as http:
            for _ in range(2):
                await safe_execute_custom_tool(
                    http,
                    name="check_balance",
                    url="https://consumer.example.com/balance",
                    args={"phone": "+1555"},
                    function_secret="s",
                    variables={},
                    state=state,
                    entry={"method": "GET"},
                )

    asyncio.run(run())
    assert len(calls) == 2


def test_same_name_different_endpoint_is_not_replayed() -> None:
    # A flow names function nodes by entry["name"] while their identity is
    # tool_id, so two nodes can share a name and point at different endpoints.
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"from": str(request.url)})

    state = CallState()

    async def run() -> None:
        async with _client(handler) as http:
            for url in ("https://a.example.com/tool", "https://b.example.com/tool"):
                await safe_execute_custom_tool(
                    http,
                    name="lookup",
                    url=url,
                    args={"q": "x"},
                    function_secret="s",
                    variables={},
                    state=state,
                )

    asyncio.run(run())
    assert seen == ["https://a.example.com/tool", "https://b.example.com/tool"]


def test_identical_raw_args_resolving_differently_are_not_replayed() -> None:
    # {{var}} resolution happens per call and `variables` mutates mid-call
    # (response_variables capture, extract_dynamic_variable), so byte-identical
    # raw args can carry different bodies.
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    state = CallState()
    variables = {"member": "margaret"}

    async def run() -> None:
        async with _client(handler) as http:
            for who in ("margaret", "mark"):
                variables["member"] = who
                await safe_execute_custom_tool(
                    http,
                    name="log_note",
                    url="https://consumer.example.com/note",
                    args={"who": "{{member}}"},
                    function_secret="s",
                    variables=variables,
                    state=state,
                )

    asyncio.run(run())
    assert [b["who"] for b in bodies] == ["margaret", "mark"]
