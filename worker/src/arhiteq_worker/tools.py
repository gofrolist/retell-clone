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
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Mapping, Protocol
from urllib.parse import urlparse

import httpx

from arhiteq_worker import metrics
from arhiteq_worker.state import CallState
from arhiteq_worker.variables import resolve_deep, resolve_template

logger = logging.getLogger("arhiteq-worker.tools")

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

# RFC 4733 DTMF event codes (LiveKit publish_dtmf takes the numeric code).
DTMF_CODES: dict[str, int] = {
    **{str(d): d for d in range(10)},
    "*": 10,
    "#": 11,
    "A": 12,
    "B": 13,
    "C": 14,
    "D": 15,
}
PRESS_DIGIT_DEFAULT_DELAY_S = 1.0
PRESS_DIGIT_MAX_DELAY_S = 5.0

CAL_API_BASE = "https://api.cal.com/v2"
# Cal.com pins endpoint behavior with a cal-api-version header per resource.
CAL_SLOTS_API_VERSION = "2024-09-04"
CAL_BOOKINGS_API_VERSION = "2024-08-13"

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"


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
    by ``ARHITEQ_ALLOW_PRIVATE_WEBHOOKS`` for local/dev, same as the backend.
    DNS is resolved on the loop's executor so it never blocks a live call.
    """
    if os.environ.get("ARHITEQ_ALLOW_PRIVATE_WEBHOOKS", "").lower() in ("1", "true", "yes"):
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

    async def end_call(
        self, reason: str = "agent_hangup", *, flush_grace: bool = False
    ) -> None: ...

    async def transfer_call(self, number: str) -> str: ...

    async def press_digit(self, digits: str) -> None: ...

    async def agent_swap(self, agent_id: str, entry: Mapping[str, Any]) -> str: ...


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


class ToolConfigError(Exception):
    """A built-in tool can't run as configured; the message is model-safe."""


def _check_response_size(resp: httpx.Response) -> str:
    if len(resp.content) > MAX_TOOL_RESPONSE_BYTES:
        raise ValueError(f"tool response too large ({len(resp.content)} bytes)")
    return resp.text


def _tool_error(name: str, exc: Exception) -> str:
    metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
    # Exception *type* only — httpx errors stringify URLs that may carry keys.
    reason = type(exc).__name__
    logger.warning("tool %s failed: %s", name, reason)
    return json.dumps({"error": f"tool call {name} failed: {reason}"})


async def _run_tool(
    name: str,
    state: CallState,
    invocation: Mapping[str, Any],
    body: Callable[[], Awaitable[str]],
) -> str:
    """Shared invoke → execute → metrics → result envelope for built-in tools.

    ``body`` returns the tool result string; raise ToolConfigError for
    config/validation failures (message goes to the model verbatim), anything
    else is redacted to its exception type.
    """
    tool_call_id = state.add_tool_invocation(name, json.dumps(dict(invocation), default=str))
    try:
        result = await body()
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="success").inc()
    except ToolConfigError as exc:
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="error").inc()
        logger.warning("tool %s rejected: %s", name, exc)
        result = json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - model sees the redacted error
        result = _tool_error(name, exc)
    state.add_tool_result(name, result, tool_call_id)
    return result


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
    return _check_response_size(resp)


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
    tool_call_id: str | None = None
    if state is not None:
        tool_call_id = state.add_tool_invocation(name, json.dumps(dict(args)))
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
        result = _tool_error(name, exc)
    if state is not None:
        state.add_tool_result(name, result, tool_call_id)
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
        tool_call_id = state.add_tool_invocation(name, "{}")
        metrics.TOOL_CALLS_TOTAL.labels(tool=name, outcome="success").inc()
        # Let any pending goodbye finish playing before hanging up. wait_for_playout
        # covers worker→room; flush_grace then covers the room→SIP→phone tail so
        # delete_room doesn't clip the last words.
        try:
            await context.wait_for_playout()
        except Exception:  # noqa: BLE001 - never block the hangup
            pass
        await control.end_call("agent_hangup", flush_grace=True)
        state.add_tool_result(name, "call ended", tool_call_id)
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
        tool_call_id = state.add_tool_invocation(name, json.dumps({"number": number}))
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
        state.add_tool_result(name, result, tool_call_id)
        return result

    return function_tool(handler, raw_schema=schema)


def press_digit_delay_s(entry: Mapping[str, Any]) -> float:
    """Retell press_digit delay_ms (0..5000, default 1000) in seconds."""
    raw = entry.get("delay_ms")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw < 0:
        return PRESS_DIGIT_DEFAULT_DELAY_S
    return min(raw / 1000.0, PRESS_DIGIT_MAX_DELAY_S)


def extract_variable_parameters(entry: Mapping[str, Any]) -> dict[str, Any]:
    """JSON schema for an extract_dynamic_variable tool's arguments.

    Retell variable specs ({name, type: string|enum|boolean|number,
    description, choices?, required?}) map 1:1 onto schema properties; the
    model fills them and the handler copies the values into the live
    dynamic-variables mapping.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for spec in entry.get("variables") or []:
        if not isinstance(spec, Mapping):
            continue
        var_name = str(spec.get("name") or "").strip()
        if not var_name:
            continue
        var_type = spec.get("type")
        prop: dict[str, Any] = {"description": str(spec.get("description") or "")}
        if var_type == "enum":
            prop["type"] = "string"
            choices = [str(c) for c in (spec.get("choices") or []) if str(c)]
            if choices:
                prop["enum"] = choices
        elif var_type in ("boolean", "number"):
            prop["type"] = var_type
        else:
            prop["type"] = "string"
        if isinstance(spec.get("examples"), list) and spec["examples"]:
            prop["description"] = (
                prop["description"] + f" Examples: {', '.join(str(e) for e in spec['examples'])}"
            ).strip()
        properties[var_name] = prop
        if spec.get("required"):
            required.append(var_name)
    return {"type": "object", "properties": properties, "required": required}


def variable_to_string(value: Any) -> str:
    """Dynamic variables are strings on the Retell wire (booleans lowercase)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def cal_event_type_id(entry: Mapping[str, Any], variables: Mapping[str, Any]) -> int | None:
    """event_type_id may be a number or a ``{{var}}`` resolved at call time."""
    raw = entry.get("event_type_id")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        resolved = resolve_template(raw, variables).strip()
        if resolved.isdigit():
            return int(resolved)
    return None


def cal_timezone(entry: Mapping[str, Any], variables: Mapping[str, Any]) -> str:
    raw = entry.get("timezone")
    if isinstance(raw, str) and raw.strip():
        return resolve_template(raw, variables).strip()
    return "UTC"


def sms_numbers(call_info: Mapping[str, Any] | None, direction_hint: str = "") -> tuple[str, str]:
    """(agent_number, user_number) for the live call.

    Inbound: the user dialed us — user is from_number; outbound the reverse.
    """
    info = call_info or {}
    direction = str(info.get("direction") or direction_hint or "outbound")
    from_number = str(info.get("from_number") or "")
    to_number = str(info.get("to_number") or "")
    if direction == "inbound":
        return to_number, from_number
    return from_number, to_number


def sms_static_content(entry: Mapping[str, Any], variables: Mapping[str, Any]) -> str | None:
    """The fixed SMS text for sms_content.type=predefined, else None.

    Inferred/template content comes from the model as a ``message`` argument
    (the content prompt is surfaced through the argument description).
    """
    content = entry.get("sms_content")
    if isinstance(content, Mapping) and content.get("type", "predefined") == "predefined":
        text = str(content.get("content") or "")
        if text:
            return resolve_template(text, variables)
    return None


def cal_default_end_date(start_date: str) -> str:
    """end_date default: one week after start_date (as the schema promises)."""
    try:
        return (date.fromisoformat(start_date) + timedelta(days=7)).isoformat()
    except ValueError:
        return start_date


def _make_press_digit_tool(entry: dict[str, Any], *, control: CallControl, state: CallState) -> Any:
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "press_digit"
    delay_s = press_digit_delay_s(entry)
    schema = {
        "type": "function",
        "name": name,
        "description": entry.get("description")
        or "Press digits on the phone keypad (DTMF), e.g. to navigate an IVR menu.",
        "parameters": {
            "type": "object",
            "properties": {
                "digit": {
                    "type": "string",
                    "description": "The digit(s) to press: 0-9, * or #.",
                }
            },
            "required": ["digit"],
        },
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        digits = str(raw_arguments.get("digit") or "")

        async def body() -> str:
            if not digits or any(d.upper() not in DTMF_CODES for d in digits):
                raise ToolConfigError("invalid digit; allowed: 0-9, *, #")
            # IVR menus speak slowly; the configured delay lets the menu
            # finish before we key in.
            await asyncio.sleep(delay_s)
            await control.press_digit(digits)
            return json.dumps({"result": f"pressed {digits}"})

        return await _run_tool(name, state, {"digit": digits}, body)

    return function_tool(handler, raw_schema=schema)


def _make_extract_dynamic_variable_tool(
    entry: dict[str, Any], *, variables: Mapping[str, Any], state: CallState
) -> Any:
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "extract_dynamic_variable"
    parameters = extract_variable_parameters(entry)
    schema = {
        "type": "function",
        "name": name,
        "description": entry.get("description")
        or "Extract variables from the conversation as soon as they are known.",
        "parameters": parameters,
    }
    known = set(parameters["properties"])

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        extracted = {
            key: variable_to_string(value)
            for key, value in raw_arguments.items()
            if key in known and value is not None
        }

        async def body() -> str:
            if isinstance(variables, dict):
                # Same mapping the session resolves {{var}} against — extracted
                # values are immediately usable in prompts and tool configs.
                variables.update(extracted)
            state.collected_dynamic_variables.update(extracted)
            return json.dumps({"result": "variables extracted", "extracted": extracted})

        return await _run_tool(name, state, extracted, body)

    return function_tool(handler, raw_schema=schema)


def _make_cal_tool(
    entry: dict[str, Any],
    *,
    kind: str,
    http: httpx.AsyncClient,
    variables: Mapping[str, Any],
    state: CallState,
) -> Any:
    """check_availability_cal / book_appointment_cal — same Cal.com plumbing,
    different request shape."""
    from livekit.agents import RunContext, function_tool

    checking = kind == "check_availability_cal"
    name = entry.get("name") or kind
    if checking:
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "First date to check, YYYY-MM-DD.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Last date to check, YYYY-MM-DD. Defaults to one week"
                    " after start_date.",
                },
            },
            "required": ["start_date"],
        }
        default_description = "Check available appointment slots on the calendar."
    else:
        parameters = {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "string",
                    "description": "Appointment start in ISO 8601 UTC, e.g. 2026-07-14T15:00:00Z.",
                },
                "name": {"type": "string", "description": "Attendee full name."},
                "email": {"type": "string", "description": "Attendee email address."},
                "phone": {
                    "type": "string",
                    "description": "Attendee phone number in E.164 format (optional).",
                },
            },
            "required": ["start_time", "name", "email"],
        }
        default_description = "Book an appointment on the calendar. Check availability first."
    schema = {
        "type": "function",
        "name": name,
        "description": resolve_template(entry.get("description") or "", variables)
        or default_description,
        "parameters": parameters,
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        async def body() -> str:
            event_type = cal_event_type_id(entry, variables)
            api_key = resolve_template(str(entry.get("cal_api_key") or ""), variables)
            if event_type is None or not api_key:
                raise ToolConfigError("calendar tool is not configured")
            headers = {
                "Authorization": f"Bearer {api_key}",
                "cal-api-version": CAL_SLOTS_API_VERSION if checking else CAL_BOOKINGS_API_VERSION,
            }
            if checking:
                start = str(raw_arguments.get("start_date") or "")
                end = str(raw_arguments.get("end_date") or "") or cal_default_end_date(start)
                resp = await http.get(
                    f"{CAL_API_BASE}/slots",
                    params={
                        "eventTypeId": event_type,
                        "start": start,
                        "end": end,
                        "timeZone": cal_timezone(entry, variables),
                    },
                    headers=headers,
                    timeout=tool_timeout_s(entry),
                )
            else:
                attendee: dict[str, Any] = {
                    "name": str(raw_arguments.get("name") or ""),
                    "email": str(raw_arguments.get("email") or ""),
                    "timeZone": cal_timezone(entry, variables),
                }
                phone = str(raw_arguments.get("phone") or "")
                if phone:
                    attendee["phoneNumber"] = phone
                resp = await http.post(
                    f"{CAL_API_BASE}/bookings",
                    json={
                        "eventTypeId": event_type,
                        "start": str(raw_arguments.get("start_time") or ""),
                        "attendee": attendee,
                    },
                    headers=headers,
                    timeout=tool_timeout_s(entry),
                )
            resp.raise_for_status()
            return _check_response_size(resp)

        return await _run_tool(name, state, raw_arguments, body)

    return function_tool(handler, raw_schema=schema)


def _make_send_sms_tool(
    entry: dict[str, Any],
    *,
    http: httpx.AsyncClient,
    variables: Mapping[str, Any],
    call_info: Mapping[str, Any] | None,
    state: CallState,
) -> Any:
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "send_sms"
    content = entry.get("sms_content") or {}
    is_predefined = sms_static_content(entry, variables) is not None
    properties: dict[str, Any] = {}
    required: list[str] = []
    if not is_predefined:
        prompt = str(content.get("prompt") or "") if isinstance(content, Mapping) else ""
        properties["message"] = {
            "type": "string",
            "description": prompt or "The SMS text to send to the user.",
        }
        required = ["message"]
    schema = {
        "type": "function",
        "name": name,
        "description": resolve_template(entry.get("description") or "", variables)
        or "Send an SMS text message to the user on this call.",
        "parameters": {"type": "object", "properties": properties, "required": required},
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        # Resolve the predefined body at CALL time so variables captured
        # mid-call (extract_dynamic_variable, response_variables) land in it.
        text = sms_static_content(entry, variables) or str(raw_arguments.get("message") or "")
        agent_number, user_number = sms_numbers(call_info)

        async def body() -> str:
            api_key = os.environ.get("TELNYX_API_KEY", "")
            if not api_key:
                raise ToolConfigError("SMS is not configured")
            if not (_E164_RE.match(agent_number or "") and _E164_RE.match(user_number or "")):
                # Web calls have no phone numbers to text between.
                raise ToolConfigError("SMS requires a phone call with E.164 numbers")
            if not text:
                raise ToolConfigError("empty SMS message")
            payload = {"from": agent_number, "to": user_number, "text": text}
            profile_id = os.environ.get("TELNYX_MESSAGING_PROFILE_ID")
            if profile_id:
                payload["messaging_profile_id"] = profile_id
            resp = await http.post(
                TELNYX_MESSAGES_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=tool_timeout_s(entry),
            )
            resp.raise_for_status()
            return json.dumps({"result": f"SMS sent to {user_number}"})

        return await _run_tool(name, state, {"to": user_number, "message": text}, body)

    return function_tool(handler, raw_schema=schema)


def _make_agent_swap_tool(entry: dict[str, Any], *, control: CallControl, state: CallState) -> Any:
    from livekit.agents import RunContext, function_tool

    name = entry.get("name") or "agent_swap"
    agent_id = str(entry.get("agent_id") or "")
    schema = {
        "type": "function",
        "name": name,
        "description": entry.get("description") or "Hand the conversation over to another agent.",
        "parameters": {"type": "object", "properties": {}},
    }

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        async def body() -> str:
            if not agent_id:
                raise ToolConfigError("no agent_id configured for agent swap")
            return await control.agent_swap(agent_id, entry)

        return await _run_tool(name, state, {"agent_id": agent_id}, body)

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
        elif tool_type == "press_digit":
            tools.append(_make_press_digit_tool(entry, control=control, state=state))
        elif tool_type == "extract_dynamic_variable":
            tools.append(
                _make_extract_dynamic_variable_tool(entry, variables=variables, state=state)
            )
        elif tool_type in ("check_availability_cal", "book_appointment_cal"):
            tools.append(
                _make_cal_tool(entry, kind=tool_type, http=http, variables=variables, state=state)
            )
        elif tool_type == "send_sms":
            tools.append(
                _make_send_sms_tool(
                    entry, http=http, variables=variables, call_info=call_info, state=state
                )
            )
        elif tool_type == "agent_swap":
            tools.append(_make_agent_swap_tool(entry, control=control, state=state))
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
            # TODO: kb_lookup → Arhiteq knowledge-base retrieval feature.
            logger.warning(
                "skipping unsupported tool %r (type=%s, no url)",
                entry.get("name"),
                tool_type,
            )
    return tools
