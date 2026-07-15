"""Dynamic-variable resolution for Retell-style ``{{key}}`` templates.

CONTRACT (docs/ARCHITECTURE.md rule 5): every dynamic variable — arbitrary
string keys and values — must reach the agent as a ``{{key}}`` template value
with no renaming and no dropping. An unknown ``{{var}}`` stays literal.

Besides user-supplied variables, Retell provides default system variables
(https://docs.retellai.com/build/dynamic-variables): time variables in a
configurable timezone (``{{current_time}}``, ``{{current_time_Asia/Tokyo}}``),
session variables (``{{session_type}}``, ``{{session_duration}}``) and
phone-call variables (``{{direction}}``, ``{{user_number}}``,
``{{agent_number}}``, ``{{call_id}}``, ``{{call_type}}``). These are
implemented by :class:`ResolutionVariables` and must be computed at lookup
time — tools resolve templates mid-call, so ``{{current_time}}`` and
``{{session_duration}}`` reflect when the placeholder is evaluated, not when
the call started.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# Retell resolves un-suffixed time variables in America/Los_Angeles.
DEFAULT_TIMEZONE = "America/Los_Angeles"
_CALENDAR_DAYS = 14
# Retell supports one level of nesting ({{current_time_{{user_timezone}}}}):
# pass 1 resolves the inner placeholder, pass 2 the outer. The cap also
# bounds expansion of ``{{...}}`` text arriving inside variable *values*.
_MAX_PASSES = 3


def resolve_template(text: str, variables: Mapping[str, Any]) -> str:
    """Replace each ``{{key}}`` in *text* with ``variables[key]``.

    Keys are matched after stripping surrounding whitespace inside the
    braces (``{{ first_name }}`` == ``{{first_name}}``). Placeholders whose
    key is not present are left untouched (literal).

    Nested placeholders resolve innermost-first: the regex cannot match a
    braced key containing braces, so ``{{current_time_{{tz}}}}`` resolves
    ``{{tz}}`` on the first pass and the outer variable on the next.
    """

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            value = variables[key]
            return value if isinstance(value, str) else str(value)
        return match.group(0)

    for _ in range(_MAX_PASSES):
        resolved = _PLACEHOLDER.sub(_sub, text)
        if resolved == text:
            break
        text = resolved
    return text


def resolve_deep(value: Any, variables: Mapping[str, Any]) -> Any:
    """Recursively resolve ``{{key}}`` in every string inside *value*.

    Dict keys are never rewritten — only string values (including strings
    nested in lists/dicts, e.g. JSON-Schema ``description`` fields and tool
    argument values). Non-string scalars pass through unchanged.
    """
    if isinstance(value, str):
        return resolve_template(value, variables)
    if isinstance(value, list):
        return [resolve_deep(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: resolve_deep(item, variables) for key, item in value.items()}
    return value


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


class ResolutionVariables(dict):
    """User + call-scoped variables with Retell system variables as fallback.

    Stored entries (user dynamic variables, then the ``call.*`` call-scoped
    facts on top) always win; Retell default system variables are computed in
    ``__missing__`` only when no stored entry matches, so a user-supplied
    ``current_time`` overrides the built-in. A ``dict`` subclass — not a
    ``Mapping`` — because tool code feature-detects ``isinstance(vars, dict)``
    before merging captured response/extract variables via ``.update()``.
    """

    def __init__(
        self,
        base: Mapping[str, Any],
        *,
        call_id: str = "",
        direction: str = "",
        from_number: str = "",
        to_number: str = "",
        call_type: str = "",
        answered_at_ms: int | None = None,
    ) -> None:
        super().__init__(base)
        inbound = direction == "inbound"
        phone_call = call_type == "phone_call"
        self._facts: dict[str, str] = {}
        if call_id:
            self._facts["call_id"] = call_id
        if call_type:
            self._facts["call_type"] = call_type
        # Phone-call-only variables: for web calls the direction column is a
        # non-null placeholder and there are no numbers — leave them literal.
        if phone_call and direction:
            self._facts["direction"] = direction
        if phone_call and (from_number or to_number):
            self._facts["user_number"] = from_number if inbound else to_number
            self._facts["agent_number"] = to_number if inbound else from_number
        self._answered_at_ms = answered_at_ms

    def _system_value(self, key: str) -> str | None:
        fact = self._facts.get(key)
        if fact:
            return fact
        if key == "session_type":
            return "voice"
        if key == "session_duration":
            # Retell: "available after call / chat starts".
            if self._answered_at_ms is None:
                return None
            return _format_duration(_utcnow().timestamp() - self._answered_at_ms / 1000.0)
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

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        return isinstance(key, str) and self._system_value(key) is not None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
