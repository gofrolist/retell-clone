"""Typed views over the call execution config
(shape: docs/INTERNAL_API.md — GET /internal/calls/{call_id}/config).

Unknown/extra fields are preserved in ``raw`` and otherwise ignored.
Defaults mirror Retell agent defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from arhiteq_worker.variables import ResolutionVariables

logger = logging.getLogger("arhiteq-worker.config")


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


def gemini_live_temperature(raw: str | None) -> float | None:
    """Parse ARHITEQ_GEMINI_LIVE_TEMPERATURE: a float in 0..2, else None.

    None means "leave the model's default sampling temperature in place".
    Retell's model_temperature is deliberately NOT forwarded to Gemini Live:
    native-audio models sample text and audio tokens with a single temperature,
    and low values (agents commonly carry Retell's text-LLM 0) degenerate the
    speech into droning/repeated syllables. Google recommends the default
    temperature for native audio; an operator can still pin one via this env.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or not 0.0 <= value <= 2.0:
        # A rejected pin is otherwise indistinguishable from an unset one in
        # the logs (the fallback is inaudible), so say it out loud.
        logger.warning(
            "ignoring ARHITEQ_GEMINI_LIVE_TEMPERATURE=%r: not a number in 0..2; "
            "Live sessions use the model default",
            raw,
        )
        return None
    return value


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
    # "Current Time Awareness": IANA zone for un-suffixed time variables.
    timezone: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
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
            timezone=_str(d.get("timezone"), "") or None,
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
    # Knowledge bases attached in the dashboard; the kb_lookup tool searches
    # these unless its own config names a narrower set.
    knowledge_base_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMConfig:
        begin = d.get("begin_message")
        return cls(
            model=_str(d.get("model"), ""),
            model_temperature=_num(d.get("model_temperature"), 0.0),
            general_prompt=_str(d.get("general_prompt"), ""),
            begin_message=begin if isinstance(begin, str) and begin else None,
            start_speaker=_str(d.get("start_speaker"), "agent"),
            general_tools=[t for t in (d.get("general_tools") or []) if isinstance(t, dict)],
            knowledge_base_ids=[
                str(i) for i in (d.get("knowledge_base_ids") or []) if isinstance(i, str) and i
            ],
            raw=d,
        )


@dataclass(slots=True)
class ConversationFlowConfig:
    global_prompt: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    start_node_id: str = ""
    start_speaker: str = "agent"
    tools: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    model_choice: dict[str, Any] | None = None
    model_temperature: float | None = None
    kb_config: dict[str, Any] | None = None
    knowledge_base_ids: list[str] = field(default_factory=list)
    default_dynamic_variables: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConversationFlowConfig:
        nodes = [n for n in (d.get("nodes") or []) if isinstance(n, dict)]
        start_node_id = _str(d.get("start_node_id"), "")
        if not start_node_id and nodes:
            first_id = nodes[0].get("id")
            start_node_id = first_id if isinstance(first_id, str) else ""
        model_temperature = d.get("model_temperature")
        return cls(
            global_prompt=_str(d.get("global_prompt"), ""),
            nodes=nodes,
            start_node_id=start_node_id,
            start_speaker=_str(d.get("start_speaker"), "agent"),
            tools=[t for t in (d.get("tools") or []) if isinstance(t, dict)],
            components=[c for c in (d.get("components") or []) if isinstance(c, dict)],
            model_choice=d.get("model_choice") if isinstance(d.get("model_choice"), dict) else None,
            model_temperature=_num(model_temperature, 0.0)
            if model_temperature is not None
            else None,
            kb_config=d.get("kb_config") if isinstance(d.get("kb_config"), dict) else None,
            knowledge_base_ids=[
                str(i) for i in (d.get("knowledge_base_ids") or []) if isinstance(i, str) and i
            ],
            default_dynamic_variables=dict(d.get("default_dynamic_variables") or {}),
            raw=d,
        )


@dataclass(slots=True)
class CallConfig:
    call_id: str
    direction: str
    from_number: str
    to_number: str
    call_type: str
    agent: AgentConfig
    llm: LLMConfig
    dynamic_variables: dict[str, Any]
    metadata: dict[str, Any]
    function_secret: str
    conversation_flow: ConversationFlowConfig | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CallConfig:
        return cls(
            call_id=_str(d.get("call_id"), ""),
            direction=_str(d.get("direction"), "outbound"),
            from_number=_str(d.get("from_number"), ""),
            to_number=_str(d.get("to_number"), ""),
            # Fail closed: without call_type the phone-vs-web gate cannot
            # decide, so ResolutionVariables exposes no call_type/direction/
            # user_number/agent_number and those placeholders stay literal
            # (matters only while an older control plane omits the field).
            call_type=_str(d.get("call_type"), ""),
            agent=AgentConfig.from_dict(d.get("agent") or {}),
            llm=LLMConfig.from_dict(d.get("llm") or {}),
            # Already merged control-plane side: defaults < call-level vars.
            dynamic_variables=dict(d.get("dynamic_variables") or {}),
            metadata=dict(d.get("metadata") or {}),
            function_secret=_str(d.get("function_secret"), ""),
            conversation_flow=(
                ConversationFlowConfig.from_dict(d["conversation_flow"])
                if isinstance(d.get("conversation_flow"), dict)
                else None
            ),
            raw=d,
        )

    def resolution_variables(self, answered_at_ms: int | None = None) -> ResolutionVariables:
        """Dynamic variables plus Retell system variables.

        Retell resolves ``{{call.call_id}}``-style placeholders from the live
        call object, and consumer tool specs depend on it (log_outcome
        requires ``retell_call_id={{call.call_id}}``). Call-scoped values are
        facts about the call, so they win over same-named user variables.
        Retell default system variables ({{current_time}}, {{direction}},
        {{session_duration}}, …) resolve lazily underneath user variables —
        see ResolutionVariables. Un-suffixed time variables use the agent's
        configured timezone ("Current Time Awareness").

        A conversation flow's own ``default_dynamic_variables`` go in
        UNDERNEATH ``dynamic_variables``, matching the single-prompt path's
        precedence (defaults < call-level, already merged control-plane side
        for an LLM-backed agent). A flow-backed agent has ``llm: null``, so
        that control-plane merge never runs for it and the flow's defaults
        would otherwise be parsed and then dropped — a greeting would speak
        the raw ``{{caller_name}}`` and every ``equation`` edge testing a
        defaulted variable would read *missing*, silently degrading equation
        routing to the else/fallback path. Nothing changes for the
        single-prompt path: without a flow this merges an empty mapping.
        """
        flow_defaults = (
            self.conversation_flow.default_dynamic_variables
            if self.conversation_flow is not None
            else {}
        )
        return ResolutionVariables(
            {
                **flow_defaults,
                **self.dynamic_variables,
                "call.call_id": self.call_id,
                "call.direction": self.direction,
                "call.from_number": self.from_number,
                "call.to_number": self.to_number,
            },
            call_id=self.call_id,
            direction=self.direction,
            from_number=self.from_number,
            to_number=self.to_number,
            call_type=self.call_type,
            answered_at_ms=answered_at_ms,
            default_timezone=self.agent.timezone,
        )

    def tool_call_object(self) -> dict[str, Any]:
        """The ``call`` object Retell sends alongside custom-function args.

        Consumer handlers fall back to ``call.call_id`` /
        ``call.from_number`` / ``call.retell_llm_dynamic_variables.phone``
        when the model omits them from the args.
        """
        return {
            "call_id": self.call_id,
            "direction": self.direction,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "retell_llm_dynamic_variables": dict(self.dynamic_variables),
            "metadata": dict(self.metadata),
        }
