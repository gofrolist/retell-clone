"""Dynamic-variable resolution for Retell-style ``{{key}}`` templates.

CONTRACT (docs/ARCHITECTURE.md rule 5): every dynamic variable — arbitrary
string keys and values — must reach the agent as a ``{{key}}`` template value
with no renaming and no dropping. An unknown ``{{var}}`` stays literal.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def resolve_template(text: str, variables: Mapping[str, Any]) -> str:
    """Replace each ``{{key}}`` in *text* with ``variables[key]``.

    Keys are matched after stripping surrounding whitespace inside the
    braces (``{{ first_name }}`` == ``{{first_name}}``). Placeholders whose
    key is not present are left untouched (literal).
    """

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            value = variables[key]
            return value if isinstance(value, str) else str(value)
        return match.group(0)

    return _PLACEHOLDER.sub(_sub, text)


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
