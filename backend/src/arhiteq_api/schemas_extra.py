"""Requests and serializers for the extended Retell API surface.

Kept separate from schemas.py to leave the frozen contract hot spots
untouched. Same conventions: `CompatModel` requests tolerate unknown fields,
serializers are plain dicts shaped 1:1 after Retell's wire format.
"""

from typing import Any

from pydantic import Field

from .models import Call, Chat, ConversationFlow, KnowledgeBase, WebhookDelivery
from .schemas import CompatModel, call_to_dict


# ── Requests ────────────────────────────────────────────────────────────────


class RegisterPhoneCallRequest(CompatModel):
    agent_id: str
    agent_version: int | str | None = None
    from_number: str | None = None
    to_number: str | None = None
    direction: str | None = None  # inbound | outbound
    metadata: dict[str, Any] | None = None
    retell_llm_dynamic_variables: dict[str, Any] | None = None


class CreateWebCallRequest(CompatModel):
    agent_id: str
    agent_version: int | str | None = None
    metadata: dict[str, Any] | None = None
    retell_llm_dynamic_variables: dict[str, Any] | None = None


class UpdateCallRequest(CompatModel):
    metadata: dict[str, Any] | None = None
    retell_llm_dynamic_variables: dict[str, Any] | None = None


class BatchCallTask(CompatModel):
    to_number: str
    retell_llm_dynamic_variables: dict[str, Any] | None = None
    ignore_e164_validation: bool = False


class CreateBatchCallRequest(CompatModel):
    from_number: str
    # Cap batch size: each task creates a Call row and (unscheduled) a live
    # dial in one request, so an unbounded list is a resource-exhaustion vector.
    tasks: list[BatchCallTask] = Field(max_length=1000)
    name: str | None = None
    trigger_timestamp: int | None = None


class CreateConversationFlowRequest(CompatModel):
    nodes: list[dict[str, Any]]
    start_speaker: str = "agent"
    model_choice: dict[str, Any] | None = None
    global_prompt: str | None = None
    start_node_id: str | None = None
    tools: list[dict[str, Any]] | None = None
    default_dynamic_variables: dict[str, Any] | None = None


class CreateChatRequest(CompatModel):
    agent_id: str
    agent_version: int | str | None = None
    metadata: dict[str, Any] | None = None
    retell_llm_dynamic_variables: dict[str, Any] | None = None


class CreateChatCompletionRequest(CompatModel):
    chat_id: str
    content: str


class ListChatsRequest(CompatModel):
    filter_criteria: dict[str, Any] | None = None
    sort_order: str = "descending"
    limit: int = Field(default=50, le=1000)
    pagination_key: str | None = None


# ── Serializers ─────────────────────────────────────────────────────────────


def web_call_to_dict(call: Call) -> dict[str, Any]:
    """The Retell V2WebCallResponse: no phone fields, plus access_token."""
    out = call_to_dict(call)
    for phone_field in ("from_number", "to_number", "direction", "custom_sip_headers"):
        out.pop(phone_field, None)
    out["access_token"] = call.access_token
    return out


def serialize_call(call: Call) -> dict[str, Any]:
    if call.call_type == "web_call":
        return web_call_to_dict(call)
    return call_to_dict(call)


def build_detail_logs(call: Call, deliveries: list[WebhookDelivery]) -> list[dict[str, Any]]:
    """Retell-style timestamped Detail Logs, synthesized from data we already
    keep (lifecycle timestamps, webhook-delivery bookkeeping, latency).

    We don't stream per-turn logs from the worker, so this is a reconstruction,
    not a live trace — enough to give the dashboard's Detail Logs tab the same
    shape Retell shows. Each entry is {time_ms, level, message}.
    """
    entries: list[dict[str, Any]] = []

    def add(time_ms: int | None, message: str, level: str = "info") -> None:
        if time_ms is not None:
            entries.append({"time_ms": time_ms, "level": level, "message": message})

    add(call.start_timestamp, f"Starting call: {call.call_id}")

    for d in deliveries:
        add(d.created_at_ms, f"Webhook triggered for {d.event}")
        if d.last_status_code is not None:
            add(
                d.created_at_ms,
                f"Webhook response received for {d.event}: HTTP {d.last_status_code}",
            )
        elif d.last_error:
            add(
                d.created_at_ms,
                f"Webhook delivery failed for {d.event}: {d.last_error}",
                level="error",
            )

    e2e = (call.latency or {}).get("e2e") or {}
    if e2e.get("p50") is not None:
        p95 = f", p95: {e2e['p95']}ms" if e2e.get("p95") is not None else ""
        add(call.end_timestamp, f"Latency — e2e p50: {e2e['p50']}ms{p95}")

    if call.disconnection_reason:
        add(call.end_timestamp, f"Disconnection reason: {call.disconnection_reason}")
    add(call.end_timestamp, f"Ending call: {call.call_id}")

    # Stable sort keeps insertion order within the same timestamp (e.g. trigger
    # before its response), so lines read in the order they logically happened.
    entries.sort(key=lambda e: e["time_ms"])
    return entries


def knowledge_base_to_dict(kb: KnowledgeBase) -> dict[str, Any]:
    return {
        "knowledge_base_id": kb.knowledge_base_id,
        "knowledge_base_name": kb.knowledge_base_name,
        "status": kb.status,
        "knowledge_base_sources": kb.sources or [],
        "enable_auto_refresh": kb.enable_auto_refresh,
        "last_refreshed_timestamp": kb.last_refreshed_timestamp,
    }


def conversation_flow_to_dict(cf: ConversationFlow) -> dict[str, Any]:
    return {
        "conversation_flow_id": cf.conversation_flow_id,
        "version": cf.version,
        "global_prompt": cf.global_prompt,
        "nodes": cf.nodes,
        "start_node_id": cf.start_node_id,
        "start_speaker": cf.start_speaker,
        "model_choice": cf.model_choice,
        "tools": cf.tools,
        "default_dynamic_variables": cf.default_dynamic_variables,
        "last_modification_timestamp": cf.last_modification_timestamp,
    }


def chat_to_dict(chat: Chat) -> dict[str, Any]:
    messages = chat.messages or []
    transcript = "\n".join(
        f"{'Agent' if m.get('role') == 'agent' else 'User'}: {m.get('content', '')}"
        for m in messages
    )
    out: dict[str, Any] = {
        "chat_id": chat.chat_id,
        "agent_id": chat.agent_id,
        "agent_version": chat.agent_version,
        "chat_status": chat.chat_status,
        "start_timestamp": chat.start_timestamp,
        "transcript": transcript,
        "message_with_tool_calls": messages,
        "metadata": chat.metadata_ or {},
        "retell_llm_dynamic_variables": chat.retell_llm_dynamic_variables or {},
    }
    if chat.end_timestamp is not None:
        out["end_timestamp"] = chat.end_timestamp
    return out
