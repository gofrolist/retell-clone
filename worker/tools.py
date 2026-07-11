"""Bridge from Retell-style tool declarations (llm.general_tools) to
livekit-agents function tools.

EXECUTION CONTRACT (docs/ARCHITECTURE.md rule 4, RETELL_INTEGRATION_MAP.md
Surface 3):
- POST to the tool's ``url`` with body = the FLAT args object — never
  ``{"args": {...}}``.
- Header ``X-Caller-Secret: <function_secret from the call config>``.
- Resolve ``{{var}}`` in string argument values before sending.
- Feed the response JSON back to the model as the tool result string.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Protocol

import httpx

import metrics
from state import CallState
from variables import resolve_deep, resolve_template

logger = logging.getLogger("architeq-worker.tools")

TOOL_TIMEOUT_S = 10.0


class CallControl(Protocol):
    """Call-control surface main.py hands to the built-in tools."""

    async def end_call(self, reason: str = "agent_hangup") -> None: ...

    async def transfer_call(self, number: str) -> str: ...


async def execute_custom_tool(
    http: httpx.AsyncClient,
    *,
    url: str,
    args: Mapping[str, Any],
    function_secret: str,
    variables: Mapping[str, Any],
    timeout: float = TOOL_TIMEOUT_S,
) -> str:
    """POST flat args to a customer tool endpoint; return the response body.

    Raises on transport errors / non-2xx — callers wrap into an
    ``{"error": ...}`` tool result for the model.
    """
    # Resolve {{var}} in string argument values (nested included).
    resolved = {key: resolve_deep(value, variables) for key, value in args.items()}
    resp = await http.post(
        url,
        json=resolved,  # CONTRACT: flat body, no "args" wrapper
        headers={"X-Caller-Secret": function_secret},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


async def safe_execute_custom_tool(
    http: httpx.AsyncClient,
    *,
    name: str,
    url: str,
    args: Mapping[str, Any],
    function_secret: str,
    variables: Mapping[str, Any],
    state: CallState | None = None,
) -> str:
    if state is not None:
        state.add_tool_invocation(name, json.dumps(dict(args)))
    try:
        result = await execute_custom_tool(
            http, url=url, args=args, function_secret=function_secret, variables=variables
        )
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="success").inc()
    except Exception as exc:  # timeout, transport, non-2xx — model sees the error
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
        logger.warning("tool %s failed: %s", name, exc)
        result = json.dumps({"error": f"tool call {name} failed: {exc}"})
    if state is not None:
        state.add_tool_result(name, result)
    return result


def _make_http_tool(
    entry: dict[str, Any],
    *,
    http: httpx.AsyncClient,
    function_secret: str,
    variables: Mapping[str, Any],
    state: CallState,
) -> Any:
    # Lazy import so the pure HTTP contract above is unit-testable without
    # livekit-agents installed.
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "custom_tool"
    url = entry["url"]
    schema = {
        "type": "function",
        "name": name,
        # {{var}} in tool descriptions and parameter descriptions is resolved
        # at build time.
        "description": resolve_template(entry.get("description") or "", variables),
        "parameters": resolve_deep(
            entry.get("parameters") or {"type": "object", "properties": {}}, variables
        ),
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        return await safe_execute_custom_tool(
            http,
            name=name,
            url=url,
            args=raw_arguments,
            function_secret=function_secret,
            variables=variables,
            state=state,
        )

    return function_tool(handler, raw_schema=schema)


def _make_end_call_tool(
    entry: dict[str, Any], *, control: CallControl, state: CallState
) -> Any:
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "end_call"
    schema = {
        "type": "function",
        "name": name,
        "description": entry.get("description")
        or "End the phone call. Use when the conversation is finished or the "
        "user asks to hang up. Say goodbye before calling this.",
        "parameters": {"type": "object", "properties": {}},
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        state.add_tool_invocation(name, "{}")
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="success").inc()
        # Let any pending goodbye finish playing before hanging up.
        try:
            await context.wait_for_playout()
        except Exception:  # noqa: BLE001 - never block the hangup
            pass
        await control.end_call("agent_hangup")
        state.add_tool_result(name, "call ended")
        return "The call has been ended."

    return function_tool(handler, raw_schema=schema)


def _make_transfer_call_tool(
    entry: dict[str, Any],
    *,
    control: CallControl,
    variables: Mapping[str, Any],
    state: CallState,
) -> Any:
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "transfer_call"
    destination = entry.get("transfer_destination") or {}
    static_number = destination.get("number") or entry.get("number")

    properties: dict[str, Any] = {}
    required: list[str] = []
    if not static_number:
        properties["number"] = {
            "type": "string",
            "description": "E.164 phone number to transfer the call to",
        }
        required = ["number"]
    schema = {
        "type": "function",
        "name": name,
        "description": resolve_template(entry.get("description") or "", variables)
        or "Transfer the call to another phone number (cold transfer).",
        "parameters": {"type": "object", "properties": properties, "required": required},
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        number = static_number or str(raw_arguments.get("number") or "")
        number = resolve_template(number, variables)
        state.add_tool_invocation(name, json.dumps({"number": number}))
        if not number:
            metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
            return json.dumps({"error": "no transfer destination configured"})
        try:
            result = await control.transfer_call(number)
            metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="success").inc()
        except Exception as exc:  # noqa: BLE001
            metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
            logger.warning("transfer to %s failed: %s", number, exc)
            result = json.dumps({"error": f"transfer failed: {exc}"})
        state.add_tool_result(name, result)
        return result

    return function_tool(handler, raw_schema=schema)


def build_tools(
    general_tools: list[dict[str, Any]],
    *,
    http: httpx.AsyncClient,
    function_secret: str,
    variables: Mapping[str, Any],
    control: CallControl,
    state: CallState,
) -> list[Any]:
    """Convert llm.general_tools declarations into livekit function tools.

    Dispatch: built-ins by declared ``type`` (Retell built-ins carry a type
    field); anything with a ``url`` is a customer HTTP tool — including
    customer tools *named* end_call that post to an endpoint
    (RETELL_INTEGRATION_MAP.md quirk). Unsupported built-ins (kb_lookup, …)
    are skipped with a log line.
    """
    tools: list[Any] = []
    for entry in general_tools or []:
        tool_type = entry.get("type") or "custom"
        if tool_type == "end_call":
            tools.append(_make_end_call_tool(entry, control=control, state=state))
        elif tool_type == "transfer_call":
            tools.append(
                _make_transfer_call_tool(
                    entry, control=control, variables=variables, state=state
                )
            )
        elif entry.get("url"):
            tools.append(
                _make_http_tool(
                    entry,
                    http=http,
                    function_secret=function_secret,
                    variables=variables,
                    state=state,
                )
            )
        else:
            # TODO: kb_lookup → Architeq knowledge-base retrieval feature.
            logger.warning(
                "skipping unsupported tool %r (type=%s, no url)",
                entry.get("name"),
                tool_type,
            )
    return tools
