"""Request bodies and response serializers, shaped 1:1 after Retell's API.

Serializers build plain dicts so the wire format is explicit. Unknown request
fields are tolerated everywhere (`extra="allow"`) — Retell consumers may send
fields we don't process, and rejecting them would break drop-in compatibility.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import WEBHOOK_EVENT_TYPES, Agent, Call, PhoneNumber, RetellLLM


class CompatModel(BaseModel):
    model_config = ConfigDict(extra="allow")


# ── Requests ────────────────────────────────────────────────────────────────


class CreatePhoneCallRequest(CompatModel):
    from_number: str
    to_number: str
    override_agent_id: str | None = None
    override_agent_version: int | None = None
    metadata: dict[str, Any] | None = None
    retell_llm_dynamic_variables: dict[str, Any] | None = None
    custom_sip_headers: dict[str, str] | None = None
    ignore_e164_validation: bool = False


class ListCallsRequest(CompatModel):
    filter_criteria: dict[str, Any] | None = None
    sort_order: str = "descending"
    limit: int = Field(default=50, le=1000)
    pagination_key: str | None = None


class CreateLLMRequest(CompatModel):
    model: str | None = None
    model_temperature: float | None = None
    general_prompt: str | None = None
    general_tools: list[dict[str, Any]] | None = None
    states: list[dict[str, Any]] | None = None
    starting_state: str | None = None
    begin_message: str | None = None
    start_speaker: str | None = None
    default_dynamic_variables: dict[str, str] | None = None
    knowledge_base_ids: list[str] | None = None
    mcps: list[dict[str, Any]] | None = None


class ResponseEngine(CompatModel):
    type: str = "retell-llm"
    llm_id: str | None = None
    version: int | None = None


class CreateAgentRequest(CompatModel):
    response_engine: ResponseEngine
    voice_id: str
    agent_name: str | None = None
    # Optional explicit id so Retell agent ids can be preserved on import
    # (spec §7: consumer env vars hold existing agent ids).
    agent_id: str | None = None
    voice_model: str | None = None
    voice_temperature: float | None = None
    voice_speed: float | None = None
    volume: float | None = None
    language: str | None = None
    responsiveness: float | None = None
    interruption_sensitivity: float | None = None
    enable_backchannel: bool | None = None
    backchannel_frequency: float | None = None
    backchannel_words: list[str] | None = None
    reminder_trigger_ms: int | None = None
    reminder_max_count: int | None = None
    ambient_sound: str | None = None
    ambient_sound_volume: float | None = None
    webhook_url: str | None = None
    # Per-agent webhook overrides (dashboard "Webhook Settings"). Additive to
    # Retell's shape. timeout: null = platform default; events: null = all.
    webhook_timeout_ms: int | None = Field(default=None, ge=1000, le=30000)
    webhook_events: list[str] | None = None
    boosted_keywords: list[str] | None = None
    pronunciation_dictionary: list[dict[str, Any]] | None = None
    normalize_for_speech: bool | None = None
    end_call_after_silence_ms: int | None = None
    max_call_duration_ms: int | None = None
    voicemail_option: dict[str, Any] | None = None
    enable_voicemail_detection: bool | None = None
    post_call_analysis_data: list[dict[str, Any]] | None = None
    post_call_analysis_model: str | None = None
    begin_message_delay_ms: int | None = None
    stt_mode: str | None = None
    denoising_mode: str | None = None
    opt_out_sensitive_data_storage: bool | None = None
    pii_config: dict[str, Any] | None = None
    fallback_voice_ids: list[str] | None = None
    allow_user_dtmf: bool | None = None
    allow_dtmf_interruption: bool | None = None
    user_dtmf_options: dict[str, Any] | None = None
    opt_in_signed_url: bool | None = None
    ivr_option: dict[str, Any] | None = None
    call_screening_option: dict[str, Any] | None = None
    # Arhiteq extra (dashboard folders); absent from Retell's public API.
    folder_id: str | None = None

    @field_validator("webhook_events")
    @classmethod
    def _known_events(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        unknown = [e for e in v if e not in WEBHOOK_EVENT_TYPES]
        if unknown:
            raise ValueError(
                f"unknown webhook event(s): {', '.join(unknown)}; "
                f"allowed: {', '.join(WEBHOOK_EVENT_TYPES)}"
            )
        # De-dupe while preserving the caller's order.
        return list(dict.fromkeys(v))


class TestWebhookRequest(CompatModel):
    """Dashboard "Test" button (Arhiteq-extra; not in Retell's public API).

    webhook_url lets the dashboard validate the on-screen, possibly-unsaved URL
    before the user saves; null falls back to the agent/workspace URL.
    """

    webhook_url: str | None = None
    webhook_timeout_ms: int | None = Field(default=None, ge=1000, le=30000)
    event: str = "call_ended"

    @field_validator("event")
    @classmethod
    def _known_event(cls, v: str) -> str:
        if v not in WEBHOOK_EVENT_TYPES:
            raise ValueError(
                f"unknown webhook event: {v}; allowed: {', '.join(WEBHOOK_EVENT_TYPES)}"
            )
        return v


class CreatePhoneNumberRequest(CompatModel):
    phone_number: str | None = None
    area_code: int | None = None
    nickname: str | None = None
    inbound_agent_id: str | None = None
    outbound_agent_id: str | None = None
    inbound_webhook_url: str | None = None
    number_provider: str | None = None


class ImportPhoneNumberRequest(CompatModel):
    phone_number: str
    termination_uri: str | None = None
    sip_trunk_auth_username: str | None = None
    sip_trunk_auth_password: str | None = None
    nickname: str | None = None
    inbound_agent_id: str | None = None
    outbound_agent_id: str | None = None
    inbound_webhook_url: str | None = None


# ── Serializers ─────────────────────────────────────────────────────────────


def call_to_dict(call: Call) -> dict[str, Any]:
    """The Retell call object: get-call response and webhook `call` payload."""
    out: dict[str, Any] = {
        "call_type": call.call_type,
        "call_id": call.call_id,
        "agent_id": call.agent_id,
        "agent_version": call.agent_version,
        "agent_name": call.agent_name,
        "call_status": call.call_status,
        "direction": call.direction,
        "from_number": call.from_number,
        "to_number": call.to_number,
        "metadata": call.metadata_ or {},
        "retell_llm_dynamic_variables": call.retell_llm_dynamic_variables or {},
        "opt_out_sensitive_data_storage": False,
    }
    if call.collected_dynamic_variables:
        out["collected_dynamic_variables"] = call.collected_dynamic_variables
    if call.custom_sip_headers:
        out["custom_sip_headers"] = call.custom_sip_headers
    if call.telephony_identifier:
        out["telephony_identifier"] = call.telephony_identifier
    if call.start_timestamp is not None:
        out["start_timestamp"] = call.start_timestamp
    if call.end_timestamp is not None:
        out["end_timestamp"] = call.end_timestamp
    if call.duration_ms is not None:
        out["duration_ms"] = call.duration_ms
    if call.disconnection_reason is not None:
        out["disconnection_reason"] = call.disconnection_reason
    if call.transcript is not None:
        out["transcript"] = call.transcript
    if call.transcript_object is not None:
        out["transcript_object"] = call.transcript_object
    if call.transcript_with_tool_calls is not None:
        out["transcript_with_tool_calls"] = call.transcript_with_tool_calls
    # recording_url may legitimately be null after a call — consumers write it
    # as-is — but only appears once the call has ended.
    if call.call_status in ("ended", "error"):
        out["recording_url"] = call.recording_url
        out["public_log_url"] = call.public_log_url
    if call.call_analysis is not None:
        analysis = dict(call.call_analysis)
        # Retell canonical key is call_summary; our known consumer reads
        # `summary`. Emit both, always in sync.
        if "call_summary" in analysis and "summary" not in analysis:
            analysis["summary"] = analysis["call_summary"]
        if "summary" in analysis and "call_summary" not in analysis:
            analysis["call_summary"] = analysis["summary"]
        out["call_analysis"] = analysis
    if call.latency is not None:
        out["latency"] = call.latency
    if call.call_cost is not None:
        out["call_cost"] = call.call_cost
    return out


def llm_to_dict(llm: RetellLLM) -> dict[str, Any]:
    return {
        "llm_id": llm.llm_id,
        "version": llm.version,
        "model": llm.model,
        "model_temperature": llm.model_temperature,
        "general_prompt": llm.general_prompt,
        "general_tools": llm.general_tools,
        "states": llm.states,
        "starting_state": llm.starting_state,
        "begin_message": llm.begin_message,
        "start_speaker": llm.start_speaker,
        "default_dynamic_variables": llm.default_dynamic_variables,
        "knowledge_base_ids": llm.knowledge_base_ids,
        "mcps": llm.mcps,
        "is_published": True,
        "last_modification_timestamp": llm.last_modification_timestamp,
    }


def agent_to_dict(agent: Agent) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "version": agent.version,
        "is_published": agent.is_published,
        "response_engine": agent.response_engine,
        "agent_name": agent.agent_name,
        "voice_id": agent.voice_id,
        "voice_model": agent.voice_model,
        "voice_temperature": agent.voice_temperature,
        "voice_speed": agent.voice_speed,
        "volume": agent.volume,
        "language": agent.language,
        "responsiveness": agent.responsiveness,
        "interruption_sensitivity": agent.interruption_sensitivity,
        "enable_backchannel": agent.enable_backchannel,
        "backchannel_frequency": agent.backchannel_frequency,
        "backchannel_words": agent.backchannel_words,
        "reminder_trigger_ms": agent.reminder_trigger_ms,
        "reminder_max_count": agent.reminder_max_count,
        "ambient_sound": agent.ambient_sound,
        "ambient_sound_volume": agent.ambient_sound_volume,
        "webhook_url": agent.webhook_url,
        "webhook_timeout_ms": agent.webhook_timeout_ms,
        "webhook_events": agent.webhook_events,
        "boosted_keywords": agent.boosted_keywords,
        "pronunciation_dictionary": agent.pronunciation_dictionary,
        "normalize_for_speech": agent.normalize_for_speech,
        "end_call_after_silence_ms": agent.end_call_after_silence_ms,
        "max_call_duration_ms": agent.max_call_duration_ms,
        "voicemail_option": agent.voicemail_option,
        "enable_voicemail_detection": agent.enable_voicemail_detection,
        "post_call_analysis_data": agent.post_call_analysis_data,
        "post_call_analysis_model": agent.post_call_analysis_model,
        "begin_message_delay_ms": agent.begin_message_delay_ms,
        "stt_mode": agent.stt_mode,
        "denoising_mode": agent.denoising_mode,
        "opt_out_sensitive_data_storage": agent.opt_out_sensitive_data_storage,
        "pii_config": agent.pii_config,
        "fallback_voice_ids": agent.fallback_voice_ids,
        "allow_user_dtmf": agent.allow_user_dtmf,
        "allow_dtmf_interruption": agent.allow_dtmf_interruption,
        "user_dtmf_options": agent.user_dtmf_options,
        "opt_in_signed_url": agent.opt_in_signed_url,
        "ivr_option": agent.ivr_option,
        "call_screening_option": agent.call_screening_option,
        "last_modification_timestamp": agent.last_modification_timestamp,
        # Arhiteq extra (dashboard folders); additive, not in Retell's shape.
        "folder_id": agent.folder_id,
    }


def phone_number_to_dict(pn: PhoneNumber) -> dict[str, Any]:
    return {
        "phone_number": pn.phone_number,
        "phone_number_pretty": pn.phone_number_pretty,
        "phone_number_type": pn.provider,
        "nickname": pn.nickname,
        "inbound_agent_id": pn.inbound_agent_id,
        "outbound_agent_id": pn.outbound_agent_id,
        "inbound_webhook_url": pn.inbound_webhook_url,
        "fallback_number": pn.fallback_number,
        "area_code": pn.area_code,
        "last_modification_timestamp": pn.last_modification_timestamp,
    }
