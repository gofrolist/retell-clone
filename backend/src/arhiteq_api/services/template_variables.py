"""Retell-style ``{{variable}}`` resolution for prompts the API itself runs.

Hand-kept mirror of worker/src/arhiteq_worker/variables.py (backend and
worker are separate packages): same placeholder grammar — inner whitespace
stripped, unknown names stay literal, one level of nesting resolved inside
the key only, substituted values never re-scanned — and the same system
variable formats, so one prompt behaves identically on voice calls and chat.

System variables per https://docs.retellai.com/build/dynamic-variables:
the ``{{current_time}}`` / ``{{current_hour}}`` / ``{{current_calendar}}``
family with ``_<IANA timezone>`` suffix variants, plus ``{{session_type}}``
and ``{{session_duration}}``. :class:`ChatVariables` adds the chat-session
ones (``{{chat_id}}``); :class:`CallVariables` adds the call-scoped ones
(``{{call_id}}``, ``{{call.call_id}}``, ``{{direction}}``) for the simulation
harness, which plays a voice call without a `calls` row behind it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# A placeholder key is brace-free text that may embed ONE inner placeholder
# (Retell's single level of nesting: {{current_time_{{user_timezone}}}}).
_PLACEHOLDER = re.compile(r"\{\{\s*((?:[^{}]|\{\{[^{}]+?\}\})+?)\s*\}\}")
_INNER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_MISSING = object()

# Retell resolves un-suffixed time variables in America/Los_Angeles unless the
# agent sets one ("Current Time Awareness").
DEFAULT_TIMEZONE = "America/Los_Angeles"
_CALENDAR_DAYS = 14


def resolve_template(text: str, variables: Mapping[str, Any]) -> str:
    """Replace each ``{{key}}`` in *text* with ``variables[key]``.

    Same semantics as the worker's resolver: whitespace inside braces is
    stripped, unknown placeholders stay literal, nesting resolves inside the
    key only, and substituted values are never re-scanned.
    """
    if "{{" not in text:
        return text

    def _lookup(key: str) -> str | None:
        value = variables.get(key, _MISSING)
        if value is _MISSING:
            return None
        return value if isinstance(value, str) else str(value)

    def _sub_inner(match: re.Match[str]) -> str:
        resolved = _lookup(match.group(1))
        return match.group(0) if resolved is None else resolved

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if "{{" in key:
            key = _INNER.sub(_sub_inner, key).strip()
            if "{" in key or "}" in key:
                return match.group(0)
        resolved = _lookup(key)
        return match.group(0) if resolved is None else resolved

    return _PLACEHOLDER.sub(_sub, text)


def resolve_deep(value: Any, variables: Mapping[str, Any]) -> Any:
    """Recursively resolve ``{{key}}`` in every string inside *value*.

    Mirror of the worker's resolver of the same name, which is what a live call
    runs over tool-call arguments — a prompt that tells the agent to pass
    ``retell_call_id={{call.call_id}}`` must not leave the placeholder literal.
    Dict keys are never rewritten; non-string scalars pass through.
    """
    if isinstance(value, str):
        return resolve_template(value, variables)
    if isinstance(value, list):
        return [resolve_deep(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: resolve_deep(item, variables) for key, item in value.items()}
    return value


def prompt_variables(text: str) -> list[str]:
    """The placeholder names *text* reads, in first-appearance order.

    Backend-only (no worker mirror): this exists so callers that have to fill a
    prompt in — test-case generation, the dashboard's variable hints — can see
    which names matter. Since an unset placeholder stays literal, a branch the
    prompt gates on one is unreachable until that name has a value.

    A nested placeholder reports its inner name, not the composed key:
    ``{{current_time_{{user_timezone}}}}`` reports ``user_timezone``, which is
    the only part a caller can address directly. Setting it *composes* the key
    but does not resolve the outer placeholder — that needs a value under the
    composed name (``current_time_America/Los_Angeles``) as well, which on a
    live call comes from the system variables rather than from the caller.
    """
    names: list[str] = []
    for match in _PLACEHOLDER.finditer(text or ""):
        key = match.group(1).strip()
        found = [m.group(1).strip() for m in _INNER.finditer(key)] if "{{" in key else [key]
        for name in found:
            if name and name not in names:
                names.append(name)
    return names


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _known_zone(name: str | None) -> str | None:
    """*name* if it is a loadable IANA zone, else None."""
    if not name:
        return None
    try:
        ZoneInfo(name)
    except KeyError, ValueError, OSError:
        return None
    return name


def _format_current_time(dt: datetime) -> str:
    """Retell format: ``Thursday, March 28, 2024 at 11:46 PM PST``."""
    hour12 = dt.hour % 12 or 12
    return f"{dt:%A}, {dt:%B} {dt.day}, {dt.year} at {hour12}:{dt:%M} {dt:%p} {dt:%Z}"


def _format_current_hour(dt: datetime) -> str:
    """Retell format: hour as a fraction, e.g. ``3.5`` for 3:30."""
    value = dt.hour + dt.minute / 60
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _format_current_calendar(dt: datetime) -> str:
    """Retell format: 14 dated lines starting with ``... PST (Today)``."""
    lines = []
    for offset in range(_CALENDAR_DAYS):
        day = dt + timedelta(days=offset)
        line = f"{day:%A}, {day:%B} {day.day}, {day.year} {day:%Z}"
        lines.append(f"{line} (Today)" if offset == 0 else line)
    return "\n".join(lines)


def _format_duration(elapsed_s: float) -> str:
    """Retell format: ``20 minutes 30 seconds`` (hours prefixed when > 0)."""
    total = max(0, int(elapsed_s))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    parts.append(f"{minutes} minute" + ("" if minutes == 1 else "s"))
    parts.append(f"{seconds} second" + ("" if seconds == 1 else "s"))
    return " ".join(parts)


_TIME_FORMATTERS = {
    "current_time": _format_current_time,
    "current_hour": _format_current_hour,
    "current_calendar": _format_current_calendar,
}


class _SystemVariables(dict):
    """User variables with Retell system variables as lazy fallback.

    Stored (user-supplied) entries win, and system values are computed in
    ``__missing__`` only when no stored entry matches — the same precedence the
    worker's ResolutionVariables applies, so a prompt reads the same way on a
    live call as it does here. ``overrides`` are the exception: Retell's dotted
    ``call.*`` keys are facts about the session, and the worker stores those on
    top of the user's variables.

    Subclasses supply ``_session_type`` plus their own keys; the time family and
    ``{{session_duration}}`` are shared.
    """

    _session_type = ""

    def __init__(
        self,
        base: Mapping[str, Any],
        *,
        overrides: Mapping[str, str] | None = None,
        fallbacks: Mapping[str, str] | None = None,
        start_timestamp_ms: int | None = None,
        default_timezone: str | None = None,
    ) -> None:
        super().__init__(base)
        # Overrides are stored verbatim, empty ones included: a session fact
        # that is genuinely empty resolves to nothing, which is what the worker
        # does with the numbers on a web call. A fallback with no value is
        # dropped so the placeholder stays literal instead of resolving to "".
        self.update(overrides or {})
        self._fallbacks = {k: v for k, v in (fallbacks or {}).items() if v}
        self._start_timestamp_ms = start_timestamp_ms
        # Agent "Current Time Awareness"; an unknown name falls back to the
        # platform default rather than making every {{current_time}} literal.
        self._default_timezone = _known_zone(default_timezone) or DEFAULT_TIMEZONE

    def _system_value(self, key: str) -> str | None:
        if key in self._fallbacks:
            return self._fallbacks[key]
        if key == "session_type" and self._session_type:
            return self._session_type
        if key == "session_duration":
            if self._start_timestamp_ms is None:
                return None
            elapsed = _utcnow().timestamp() - self._start_timestamp_ms / 1000.0
            return _format_duration(elapsed)
        formatter = _TIME_FORMATTERS.get(key)
        zone_name = self._default_timezone
        if formatter is None:
            for name, fmt in _TIME_FORMATTERS.items():
                if key.startswith(name + "_"):
                    formatter, zone_name = fmt, key[len(name) + 1 :]
                    break
        if formatter is None:
            return None
        try:
            zone = ZoneInfo(zone_name)
        except KeyError, ValueError, OSError:
            return None  # unknown timezone suffix -> placeholder stays literal
        return formatter(_utcnow().astimezone(zone))

    def __missing__(self, key: str) -> str:
        value = self._system_value(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        return isinstance(key, str) and self._system_value(key) is not None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


class ChatVariables(_SystemVariables):
    """Chat-session flavour: ``{{chat_id}}`` and ``session_type`` = "chat"."""

    _session_type = "chat"

    def __init__(
        self,
        base: Mapping[str, Any],
        *,
        chat_id: str = "",
        start_timestamp_ms: int | None = None,
        default_timezone: str | None = None,
    ) -> None:
        super().__init__(
            base,
            fallbacks={"chat_id": chat_id},
            start_timestamp_ms=start_timestamp_ms,
            default_timezone=default_timezone,
        )


class CallVariables(_SystemVariables):
    """Voice-call flavour, for prompts the API runs without a live call.

    The simulation harness plays a phone call that no `calls` row and no worker
    ever back, so the call-scoped placeholders a live call resolves —
    ``{{call_id}}``, ``{{call_type}}`` and Retell's dotted ``call.*`` family —
    have to come from here instead. Prompts pass them straight into tool
    arguments (``retell_call_id={{call.call_id}}``), so leaving them literal is
    visible in every mocked tool call.

    The whole dotted family is answered, numbers included, because that is what
    the worker stores; a simulated call has no phone leg, so they answer empty
    exactly as they do on a web call. What is *not* answered is anything that
    would amount to inventing a fact: ``{{direction}}`` (which way a simulated
    call was placed is not knowable — `start_speaker` describes who talks
    first, not who dialled) and ``{{user_number}}`` / ``{{agent_number}}``.
    Those stay literal unless the scenario sets them, which is how every other
    unknown behaves here.

    Precedence copies the worker exactly, including the asymmetry: the dotted
    ``call.*`` keys are stored on top of the scenario's variables, while the
    plain ones are fallbacks a scenario may override.
    """

    _session_type = "voice"

    def __init__(
        self,
        base: Mapping[str, Any],
        *,
        call_id: str = "",
        start_timestamp_ms: int | None = None,
        default_timezone: str | None = None,
    ) -> None:
        super().__init__(
            base,
            overrides={
                "call.call_id": call_id,
                "call.direction": "",
                "call.from_number": "",
                "call.to_number": "",
            },
            fallbacks={"call_id": call_id, "call_type": "phone_call"},
            start_timestamp_ms=start_timestamp_ms,
            default_timezone=default_timezone,
        )
