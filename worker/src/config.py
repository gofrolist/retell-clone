"""Typed views over the call execution config
(shape: docs/INTERNAL_API.md — GET /internal/calls/{call_id}/config).

Unknown/extra fields are preserved in ``raw`` and otherwise ignored.
Defaults mirror Retell agent defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _str(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


@dataclass(slots=True)
class AgentConfig:
    voice_id: str = ""
    language: str = "en-US"
    voice_speed: float = 1.0
    voice_temperature: float = 1.0
    interruption_sensitivity: float = 1.0
    responsiveness: float = 1.0
    enable_backchannel: bool = False
    max_call_duration_ms: int = 3_600_000
    end_call_after_silence_ms: int = 600_000
    enable_voicemail_detection: bool = False
    voicemail_option: dict[str, Any] | None = None
    boosted_keywords: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentConfig":
        return cls(
            voice_id=_str(d.get("voice_id"), ""),
            language=_str(d.get("language"), "en-US"),
            voice_speed=_num(d.get("voice_speed"), 1.0),
            voice_temperature=_num(d.get("voice_temperature"), 1.0),
            interruption_sensitivity=_num(d.get("interruption_sensitivity"), 1.0),
            responsiveness=_num(d.get("responsiveness"), 1.0),
            enable_backchannel=bool(d.get("enable_backchannel", False)),
            max_call_duration_ms=_int(d.get("max_call_duration_ms"), 3_600_000),
            end_call_after_silence_ms=_int(d.get("end_call_after_silence_ms"), 600_000),
            enable_voicemail_detection=bool(d.get("enable_voicemail_detection", False)),
            voicemail_option=d.get("voicemail_option")
            if isinstance(d.get("voicemail_option"), dict)
            else None,
            boosted_keywords=list(d.get("boosted_keywords") or []),
            raw=d,
        )


@dataclass(slots=True)
class LLMConfig:
    model: str = ""
    model_temperature: float = 0.0
    general_prompt: str = ""
    begin_message: str | None = None
    start_speaker: str = "agent"
    general_tools: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LLMConfig":
        begin = d.get("begin_message")
        return cls(
            model=_str(d.get("model"), ""),
            model_temperature=_num(d.get("model_temperature"), 0.0),
            general_prompt=_str(d.get("general_prompt"), ""),
            begin_message=begin if isinstance(begin, str) and begin else None,
            start_speaker=_str(d.get("start_speaker"), "agent"),
            general_tools=[t for t in (d.get("general_tools") or []) if isinstance(t, dict)],
            raw=d,
        )


@dataclass(slots=True)
class CallConfig:
    call_id: str
    direction: str
    from_number: str
    to_number: str
    agent: AgentConfig
    llm: LLMConfig
    dynamic_variables: dict[str, Any]
    metadata: dict[str, Any]
    function_secret: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CallConfig":
        return cls(
            call_id=_str(d.get("call_id"), ""),
            direction=_str(d.get("direction"), "outbound"),
            from_number=_str(d.get("from_number"), ""),
            to_number=_str(d.get("to_number"), ""),
            agent=AgentConfig.from_dict(d.get("agent") or {}),
            llm=LLMConfig.from_dict(d.get("llm") or {}),
            # Already merged control-plane side: defaults < call-level vars.
            dynamic_variables=dict(d.get("dynamic_variables") or {}),
            metadata=dict(d.get("metadata") or {}),
            function_secret=_str(d.get("function_secret"), ""),
            raw=d,
        )
