"""Retell-style ``{{variable}}`` resolution for chat prompts.

Hand-kept mirror of worker/src/architeq_worker/variables.py (backend and
worker are separate packages): same placeholder grammar — inner whitespace
stripped, unknown names stay literal, one level of nesting resolved inside
the key only, substituted values never re-scanned — and the same system
variable formats, so one prompt behaves identically on voice calls and chat.
Chat-session system variables per https://docs.retellai.com/build/dynamic-variables:
``{{chat_id}}``, ``{{session_type}}`` (= "chat"), ``{{session_duration}}``,
and the ``{{current_time}}`` / ``{{current_hour}}`` / ``{{current_calendar}}``
family with ``_<IANA timezone>`` suffix variants.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

# A placeholder key is brace-free text that may embed ONE inner placeholder
# (Retell's single level of nesting: {{current_time_{{user_timezone}}}}).
_PLACEHOLDER = re.compile(r"\{\{\s*((?:[^{}]|\{\{[^{}]+?\}\})+?)\s*\}\}")
_INNER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_MISSING = object()

# Retell resolves un-suffixed time variables in America/Los_Angeles.
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class ChatVariables(dict):
    """User variables with Retell chat system variables as lazy fallback.

    Stored (user-supplied) entries always win; system values are computed in
    ``__missing__`` only when no stored entry matches.
    """

    def __init__(
        self,
        base: Mapping[str, Any],
        *,
        chat_id: str = "",
        start_timestamp_ms: int | None = None,
    ) -> None:
        super().__init__(base)
        self._chat_id = chat_id
        self._start_timestamp_ms = start_timestamp_ms

    def _system_value(self, key: str) -> str | None:
        if key == "chat_id" and self._chat_id:
            return self._chat_id
        if key == "session_type":
            return "chat"
        if key == "session_duration":
            if self._start_timestamp_ms is None:
                return None
            elapsed = _utcnow().timestamp() - self._start_timestamp_ms / 1000.0
            return _format_duration(elapsed)
        formatter = _TIME_FORMATTERS.get(key)
        zone_name = DEFAULT_TIMEZONE
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

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
