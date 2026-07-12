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

# NOTE: no `from __future__ import annotations` here. It would stringize the
# `context: RunContext` annotations on the tool handlers, and livekit-agents
# resolves those hints at execution time against this module's globals —
# where RunContext is deliberately not imported (tests run without the
# livekit stack). Python 3.14 (PEP 649) evaluates the annotation lazily via
# the handler's closure, where the factory's local import IS visible.
import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

import httpx

from architeq_worker import metrics
from architeq_worker.state import CallState
from architeq_worker.variables import resolve_deep, resolve_template

logger = logging.getLogger("architeq-worker.tools")

TOOL_TIMEOUT_S = 10.0
# Retell allows 1s..10min for a custom tool's timeout_ms. When the tool does
# not set one we keep our stricter 10s default (a stalled endpoint stalls a
# live phone call); an explicit Retell-style timeout_ms wins, clamped.
TOOL_TIMEOUT_MIN_S = 1.0
TOOL_TIMEOUT_MAX_S = 600.0
_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Cap the tool response body we buffer and feed back to the LLM: a buggy or
# hostile endpoint returning tens of MB would stall the live call and blow up
# token cost. 256 KiB is far more than any real tool result.
MAX_TOOL_RESPONSE_BYTES = 256 * 1024
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


class UnsafeToolUrlError(ValueError):
    pass


def _is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def assert_tool_url_safe(url: str) -> None:
    """Reject custom-tool URLs that resolve to non-public addresses (SSRF).

    Mirrors the backend webhook guard so a tenant can't point a tool at the
    pod network, Cloud SQL, or the metadata server (169.254.169.254). Bypassed
    by ``ARCHITEQ_ALLOW_PRIVATE_WEBHOOKS`` for local/dev, same as the backend.
    DNS is resolved on the loop's executor so it never blocks a live call.
    """
    if os.environ.get("ARCHITEQ_ALLOW_PRIVATE_WEBHOOKS", "").lower() in ("1", "true", "yes"):
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeToolUrlError(f"unsupported scheme {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeToolUrlError("missing host")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeToolUrlError(f"cannot resolve {host}") from exc
    for info in infos:
        addr = info[4][0]
        if not _is_public_ip(addr):
            raise UnsafeToolUrlError(f"{host} resolves to non-public address {addr}")


class CallControl(Protocol):
    """Call-control surface main.py hands to the built-in tools."""

    async def end_call(self, reason: str = "agent_hangup") -> None: ...

    async def transfer_call(self, number: str) -> str: ...


def tool_timeout_s(entry: Mapping[str, Any]) -> float:
    """Per-tool timeout: Retell timeout_ms clamped to 1s..600s, else 10s."""
    raw = entry.get("timeout_ms")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw <= 0:
        return TOOL_TIMEOUT_S
    return min(max(raw / 1000.0, TOOL_TIMEOUT_MIN_S), TOOL_TIMEOUT_MAX_S)


def extract_response_variables(
    response_text: str, response_variables: Mapping[str, str]
) -> dict[str, str]:
    """Retell response_variables: {"var": "data.user.name"} JSON-path lookups.

    Dot-separated path segments; integer segments index into lists. Missing
    paths and non-JSON responses are skipped silently (Retell does the same —
    the variable just stays unset).
    """
    out: dict[str, str] = {}
    if not response_variables:
        return out
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError, ValueError:
        return out
    for var, path in response_variables.items():
        node: Any = payload
        for segment in str(path).split("."):
            if isinstance(node, dict) and segment in node:
                node = node[segment]
            elif isinstance(node, list) and segment.lstrip("-").isdigit():
                try:
                    node = node[int(segment)]
                except IndexError:
                    node = None
                    break
            else:
                node = None
                break
        if node is not None and not isinstance(node, (dict, list)):
            out[str(var)] = str(node)
    return out


async def execute_custom_tool(
    http: httpx.AsyncClient,
    *,
    url: str,
    args: Mapping[str, Any],
    function_secret: str,
    variables: Mapping[str, Any],
    call_info: Mapping[str, Any] | None = None,
    timeout: float = TOOL_TIMEOUT_S,
    method: str = "POST",
    headers: Mapping[str, Any] | None = None,
    query_params: Mapping[str, Any] | None = None,
    name: str = "custom_tool",
    wrap_args: bool = False,
) -> str:
    """Send the tool request to a customer endpoint; return the response body.

    Body shape (docs/ARCHITECTURE.md rule 4): FLAT args + the ``call`` object,
    no ``{"args": ...}`` wrapper — Retell's "Payload: args only" mode, which is
    what the consumer's endpoints expect. Tools that explicitly declare
    ``args_at_root: false`` get Retell's wrapped ``{name, call, args}`` shape
    instead (``wrap_args=True``).

    Raises on transport errors / non-2xx — callers wrap into an
    ``{"error": ...}`` tool result for the model.
    """
    await assert_tool_url_safe(url)
    # Resolve {{var}} in string argument values (nested included).
    resolved = {key: resolve_deep(value, variables) for key, value in args.items()}
    body: dict[str, Any]
    if wrap_args:
        body = {"name": name, "args": resolved}
    else:
        body = resolved
    if call_info is not None:
        # Retell parity: the call object rides alongside the args so consumer
        # fallback chains (call.call_id, call.from_number, …) work.
        body["call"] = dict(call_info)
    send_headers = {str(k): resolve_template(str(v), variables) for k, v in (headers or {}).items()}
    # The shared secret is the auth contract — a tool config must not be able
    # to drop or spoof it, so it is applied after the custom headers.
    send_headers["X-Caller-Secret"] = function_secret
    params = {
        str(k): resolve_template(str(v), variables) for k, v in (query_params or {}).items()
    } or None
    verb = method.upper() if isinstance(method, str) and method else "POST"
    if verb not in _ALLOWED_METHODS:
        verb = "POST"
    request_kwargs: dict[str, Any] = {
        "headers": send_headers,
        "params": params,
        "timeout": timeout,
    }
    if verb not in ("GET", "DELETE"):
        request_kwargs["json"] = body
    resp = await http.request(verb, url, **request_kwargs)
    resp.raise_for_status()
    if len(resp.content) > MAX_TOOL_RESPONSE_BYTES:
        raise ValueError(f"tool response too large ({len(resp.content)} bytes)")
    return resp.text


async def safe_execute_custom_tool(
    http: httpx.AsyncClient,
    *,
    name: str,
    url: str,
    args: Mapping[str, Any],
    function_secret: str,
    variables: Mapping[str, Any],
    call_info: Mapping[str, Any] | None = None,
    state: CallState | None = None,
    entry: Mapping[str, Any] | None = None,
) -> str:
    entry = entry or {}
    if state is not None:
        state.add_tool_invocation(name, json.dumps(dict(args)))
    try:
        result = await execute_custom_tool(
            http,
            url=url,
            args=args,
            function_secret=function_secret,
            variables=variables,
            call_info=call_info,
            timeout=tool_timeout_s(entry),
            method=entry.get("method") or "POST",
            headers=entry.get("headers"),
            query_params=entry.get("query_params"),
            name=name,
            # Retell wraps as {name, call, args} unless args_at_root is set;
            # our default is the flat shape the consumer contract froze, so
            # only an EXPLICIT args_at_root=false opts into the wrapper.
            wrap_args=entry.get("args_at_root") is False,
        )
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="success").inc()
        captured = extract_response_variables(result, entry.get("response_variables") or {})
        if captured and isinstance(variables, dict):
            # Later {{var}} references (tool args, transfer destinations)
            # resolve against the same mapping the session was built with.
            variables.update(captured)
    except Exception as exc:  # timeout, transport, non-2xx — model sees the error
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
        # Log/return the exception *type* only: httpx errors stringify the full
        # URL, which can carry auth tokens in the query string (and would then
        # land in logs and the persisted transcript).
        reason = type(exc).__name__
        logger.warning("tool %s failed: %s", name, reason)
        result = json.dumps({"error": f"tool call {name} failed: {reason}"})
    if state is not None:
        state.add_tool_result(name, result)
    return result


def _make_http_tool(
    entry: dict[str, Any],
    *,
    http: httpx.AsyncClient,
    function_secret: str,
    variables: Mapping[str, Any],
    call_info: Mapping[str, Any] | None,
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

    speak_during = bool(entry.get("speak_during_execution"))
    execution_message = entry.get("execution_message_description") or ""
    # execution_message_type "prompt" would have the LLM phrase the filler
    # itself; we approximate both modes with a spoken sentence (static text
    # when provided, else a generic one) — good enough to keep the caller
    # from hearing dead air during a slow tool.
    filler = (
        resolve_template(execution_message, variables)
        if entry.get("execution_message_type") == "static_text" and execution_message
        else "One moment, let me check that."
    )
    speak_after = entry.get("speak_after_execution")

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str | None:
        if speak_during:
            try:
                context.session.say(filler, add_to_chat_ctx=False)
            except Exception:  # noqa: BLE001 - filler speech must never break the tool
                logger.debug("speak_during_execution failed for %s", name, exc_info=True)
        result = await safe_execute_custom_tool(
            http,
            name=name,
            url=url,
            args=raw_arguments,
            function_secret=function_secret,
            variables=variables,
            call_info=call_info,
            state=state,
            entry=entry,
        )
        if speak_after is False:
            # Retell: speak_after_execution=false → the agent does not respond
            # to the tool result. StopResponse is livekit's mechanism for that.
            try:
                from livekit.agents.llm import StopResponse

                raise StopResponse()
            except ImportError:
                logger.debug("StopResponse unavailable; returning result for %s", name)
        return result

    return function_tool(handler, raw_schema=schema)


def _make_end_call_tool(entry: dict[str, Any], *, control: CallControl, state: CallState) -> Any:
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
        if not _E164_RE.match(number):
            # The destination may come from LLM output steered by untrusted
            # caller speech — reject anything not strict E.164 so a social-
            # engineered call can't dial premium-rate/international numbers.
            metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
            logger.warning("transfer rejected: destination is not E.164")
            return json.dumps({"error": "invalid transfer destination"})
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
    call_info: Mapping[str, Any] | None = None,
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
                _make_transfer_call_tool(entry, control=control, variables=variables, state=state)
            )
        elif entry.get("url"):
            tools.append(
                _make_http_tool(
                    entry,
                    http=http,
                    function_secret=function_secret,
                    variables=variables,
                    call_info=call_info,
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
