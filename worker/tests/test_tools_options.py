"""Retell custom-tool option parity: method, timeout_ms, headers,
query_params, args_at_root, response_variables (docs.retellai.com
custom-function schema)."""

import asyncio
import json

import httpx

from arhiteq_worker.tools import (
    TOOL_TIMEOUT_S,
    execute_custom_tool,
    extract_response_variables,
    safe_execute_custom_tool,
    tool_timeout_s,
)

URL = "https://consumer.example.com/functions/v1/tool"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


def test_method_get_sends_no_body_and_appends_query_params() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http,
                url=URL,
                args={},
                function_secret="s",
                variables={"page": "2"},
                method="GET",
                query_params={"page": "{{page}}", "sort": "asc"},
            )

    _run(run())
    assert captured["method"] == "GET"
    assert "page=2" in captured["url"] and "sort=asc" in captured["url"]
    assert captured["body"] == b""


def test_unknown_method_falls_back_to_post() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        return httpx.Response(200, json={})

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http, url=URL, args={}, function_secret="s", variables={}, method="TRACE"
            )

    _run(run())
    assert captured["method"] == "POST"


def test_custom_headers_sent_but_secret_not_overridable() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["secret"] = request.headers.get("X-Caller-Secret")
        return httpx.Response(200, json={})

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http,
                url=URL,
                args={},
                function_secret="real-secret",
                variables={"token": "tok123"},
                headers={"Authorization": "Bearer {{token}}", "X-Caller-Secret": "spoofed"},
            )

    _run(run())
    assert captured["auth"] == "Bearer tok123"
    assert captured["secret"] == "real-secret"


def test_wrap_args_uses_retell_wrapper_shape() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async def run() -> None:
        async with _client(handler) as http:
            await execute_custom_tool(
                http,
                url=URL,
                args={"outcome": "answered"},
                function_secret="s",
                variables={},
                call_info={"call_id": "call_1"},
                name="log_outcome",
                wrap_args=True,
            )

    _run(run())
    assert captured["body"] == {
        "name": "log_outcome",
        "args": {"outcome": "answered"},
        "call": {"call_id": "call_1"},
    }


def test_safe_execute_wraps_only_on_explicit_args_at_root_false() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async def run() -> None:
        async with _client(handler) as http:
            for entry in ({}, {"args_at_root": True}, {"args_at_root": False}):
                await safe_execute_custom_tool(
                    http,
                    name="t",
                    url=URL,
                    args={"a": 1},
                    function_secret="s",
                    variables={},
                    entry=entry,
                )

    _run(run())
    assert bodies[0] == {"a": 1}  # absent → flat (frozen consumer contract)
    assert bodies[1] == {"a": 1}  # explicit true → flat
    assert bodies[2] == {"name": "t", "args": {"a": 1}}  # explicit false → wrapped


def test_response_variables_update_variables_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": {"name": "Ann"}, "items": [{"id": 7}]}})

    variables: dict = {"existing": "x"}

    async def run() -> str:
        async with _client(handler) as http:
            return await safe_execute_custom_tool(
                http,
                name="t",
                url=URL,
                args={},
                function_secret="s",
                variables=variables,
                entry={
                    "response_variables": {
                        "user_name": "data.user.name",
                        "first_item": "data.items.0.id",
                        "missing": "data.nope.deep",
                    }
                },
            )

    _run(run())
    assert variables["user_name"] == "Ann"
    assert variables["first_item"] == "7"
    assert "missing" not in variables
    assert variables["existing"] == "x"


def test_extract_response_variables_ignores_non_json() -> None:
    assert extract_response_variables("plain text", {"v": "a.b"}) == {}


def test_tool_timeout_clamping() -> None:
    assert tool_timeout_s({}) == TOOL_TIMEOUT_S
    assert tool_timeout_s({"timeout_ms": 120000}) == 120.0
    assert tool_timeout_s({"timeout_ms": 100}) == 1.0  # below Retell min
    assert tool_timeout_s({"timeout_ms": 10_000_000}) == 600.0  # above Retell max
    assert tool_timeout_s({"timeout_ms": True}) == TOOL_TIMEOUT_S
    assert tool_timeout_s({"timeout_ms": "fast"}) == TOOL_TIMEOUT_S
