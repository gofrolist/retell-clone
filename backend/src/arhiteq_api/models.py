import time
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .ids import (
    new_agent_id,
    new_alert_id,
    new_batch_call_id,
    new_call_id,
    new_chat_id,
    new_cohort_id,
    new_contact_id,
    new_conversation_flow_id,
    new_folder_id,
    new_invite_id,
    new_invite_token,
    new_knowledge_base_id,
    new_llm_id,
    new_phone_number_id,
    new_workspace_id,
)


def now_ms() -> int:
    return int(time.time() * 1000)


# The full Retell webhook-event catalog offered in the dashboard "Set Up"
# selector. An agent's `webhook_events` subscription may only name events from
# this set; null means "all of them". The worker currently *fires* only
# call_started / call_ended / call_analyzed; the transcript/transfer events are
# accepted for Retell parity and will deliver once the worker emits them.
WEBHOOK_EVENT_TYPES = (
    "call_started",
    "call_ended",
    "call_analyzed",
    "transcript_updated",
    "transfer_started",
    "transfer_bridged",
    "transfer_cancelled",
    "transfer_ended",
)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_workspace_id)
    name: Mapped[str] = mapped_column(String(255), default="Default workspace")
    # Workspace-level webhook for call events (agent-level URL takes precedence).
    webhook_url: Mapped[str | None] = mapped_column(Text)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="workspace")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    # SHA-256 of the key for lookup; the plaintext is shown once at creation.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # The key doubles as the webhook HMAC secret (Retell semantics), so we keep
    # an encrypted-at-rest copy for signing. Encryption is a deployment concern
    # (Cloud KMS envelope); dev stores it verbatim.
    key_material: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255), default="API key")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    workspace: Mapped[Workspace] = relationship(back_populates="api_keys")


class WorkspaceMember(Base):
    """Dashboard user with access to a workspace.

    Rows are created on first Google login (allowlisted emails become owners)
    or by accepting an invite. Membership itself grants dashboard login, so
    invited users don't need to be on the email allowlist.
    """

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)  # stored lowercase
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="member")  # owner | admin | member
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class WorkspaceInvite(Base):
    """Pending invitation to join a workspace, redeemed via Google Sign-In.

    Link-based: the dashboard builds /login?invite=<token> for the inviter to
    share. The token is not the gate — accepting requires signing in with the
    exact invited email (Google-verified), mirroring usan-voice-engine.
    Expiry is lazy (checked at accept); accepted/revoked rows are kept as
    history, and a partial unique index allows one live invite per email.
    """

    __tablename__ = "workspace_invites"
    __table_args__ = (
        Index(
            "uq_workspace_invites_pending",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_invite_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))  # stored lowercase
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=new_invite_token
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|accepted|revoked
    invited_by: Mapped[str | None] = mapped_column(String(320))
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    accepted_at_ms: Mapped[int | None] = mapped_column(BigInteger)


class RetellLLM(Base):
    """Response engine config (Retell `retell-llm` object)."""

    __tablename__ = "retell_llms"

    llm_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_llm_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="gemini-2.5-flash")
    model_temperature: Mapped[float] = mapped_column(Float, default=0.0)
    general_prompt: Mapped[str | None] = mapped_column(Text)
    begin_message: Mapped[str | None] = mapped_column(Text)
    start_speaker: Mapped[str] = mapped_column(String(16), default="agent")
    # Tool declarations: list of {type, name, description, url, method, parameters,
    # speak_during_execution, speak_after_execution, ...} — stored verbatim.
    general_tools: Mapped[list[Any] | None] = mapped_column(JSON)
    states: Mapped[list[Any] | None] = mapped_column(JSON)
    starting_state: Mapped[str | None] = mapped_column(String(255))
    default_dynamic_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    knowledge_base_ids: Mapped[list[Any] | None] = mapped_column(JSON)
    last_modification_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class AgentFolder(Base):
    """Dashboard-only grouping for agents (Retell's sidebar folders)."""

    __tablename__ = "agent_folders"
    __table_args__ = (UniqueConstraint("workspace_id", "folder_name"),)

    folder_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_folder_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    folder_name: Mapped[str] = mapped_column(String(255))
    last_modification_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_agent_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    agent_name: Mapped[str | None] = mapped_column(String(255))
    # response_engine: {"type": "retell-llm", "llm_id": ...}
    response_engine: Mapped[dict[str, Any]] = mapped_column(JSON)
    voice_id: Mapped[str] = mapped_column(String(128), default="cartesia-sonic-english")
    voice_model: Mapped[str | None] = mapped_column(String(128))
    voice_temperature: Mapped[float] = mapped_column(Float, default=1.0)
    voice_speed: Mapped[float] = mapped_column(Float, default=1.0)
    volume: Mapped[float] = mapped_column(Float, default=1.0)
    language: Mapped[str] = mapped_column(String(32), default="en-US")
    responsiveness: Mapped[float] = mapped_column(Float, default=1.0)
    interruption_sensitivity: Mapped[float] = mapped_column(Float, default=1.0)
    enable_backchannel: Mapped[bool] = mapped_column(Boolean, default=False)
    backchannel_frequency: Mapped[float] = mapped_column(Float, default=0.8)
    backchannel_words: Mapped[list[Any] | None] = mapped_column(JSON)
    reminder_trigger_ms: Mapped[int] = mapped_column(BigInteger, default=10000)
    reminder_max_count: Mapped[int] = mapped_column(Integer, default=1)
    ambient_sound: Mapped[str | None] = mapped_column(String(64))
    ambient_sound_volume: Mapped[float] = mapped_column(Float, default=1.0)
    webhook_url: Mapped[str | None] = mapped_column(Text)
    # Per-agent overrides for outbound webhooks (Retell "Webhook Settings").
    # webhook_timeout_ms: null -> platform default (config.webhook_timeout_seconds).
    # webhook_events: null -> deliver every event; a list restricts to those
    # event names (backward compatible — pre-existing agents stay null).
    webhook_timeout_ms: Mapped[int | None] = mapped_column(BigInteger)
    webhook_events: Mapped[list[Any] | None] = mapped_column(JSON)
    boosted_keywords: Mapped[list[Any] | None] = mapped_column(JSON)
    pronunciation_dictionary: Mapped[list[Any] | None] = mapped_column(JSON)
    normalize_for_speech: Mapped[bool] = mapped_column(Boolean, default=True)
    end_call_after_silence_ms: Mapped[int] = mapped_column(BigInteger, default=600000)
    max_call_duration_ms: Mapped[int] = mapped_column(BigInteger, default=3600000)
    interruption_threshold_ms: Mapped[int | None] = mapped_column(BigInteger)
    voicemail_option: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    enable_voicemail_detection: Mapped[bool] = mapped_column(Boolean, default=True)
    post_call_analysis_data: Mapped[list[Any] | None] = mapped_column(JSON)
    post_call_analysis_model: Mapped[str | None] = mapped_column(String(64))
    begin_message_delay_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    stt_mode: Mapped[str] = mapped_column(String(32), default="fast")
    denoising_mode: Mapped[str] = mapped_column(String(32), default="noise-cancellation")
    opt_out_sensitive_data_storage: Mapped[bool] = mapped_column(Boolean, default=False)
    last_modification_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    # Publishing model: single live version; publish-agent flips this on.
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    # Dashboard folder assignment (no FK: added post-launch via the lifespan
    # column backfill, and a dangling id just means "no folder").
    folder_id: Mapped[str | None] = mapped_column(String(64), index=True)


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"

    phone_number: Mapped[str] = mapped_column(String(20), primary_key=True)  # E.164
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, default=new_phone_number_id)
    phone_number_pretty: Mapped[str | None] = mapped_column(String(32))
    nickname: Mapped[str | None] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32), default="telnyx")
    inbound_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.agent_id"))
    outbound_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.agent_id"))
    # Surface 2A: pre-call routing webhook. If set, inbound calls ask this URL
    # which agent to run. `inbound_webhook_secret_in_query` appends
    # ?caller_secret=<secret> (reserved Retell-compat mechanism, off by default).
    inbound_webhook_url: Mapped[str | None] = mapped_column(Text)
    inbound_webhook_secret_in_query: Mapped[bool] = mapped_column(Boolean, default=False)
    area_code: Mapped[int | None] = mapped_column(Integer)
    last_modification_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class Call(Base):
    __tablename__ = "calls"

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_call_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    call_type: Mapped[str] = mapped_column(String(16), default="phone_call")
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[int] = mapped_column(Integer, default=0)
    agent_name: Mapped[str | None] = mapped_column(String(255))
    call_status: Mapped[str] = mapped_column(String(24), default="registered", index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    from_number: Mapped[str | None] = mapped_column(String(20), index=True)
    to_number: Mapped[str | None] = mapped_column(String(20), index=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    retell_llm_dynamic_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Variables extracted mid-call by the extract_dynamic_variable tool (worker
    # sends these on finalize). Distinct from the input vars above.
    collected_dynamic_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    custom_sip_headers: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    start_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    end_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    disconnection_reason: Mapped[str | None] = mapped_column(String(64))
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_object: Mapped[list[Any] | None] = mapped_column(JSON)
    transcript_with_tool_calls: Mapped[list[Any] | None] = mapped_column(JSON)
    recording_url: Mapped[str | None] = mapped_column(Text)
    public_log_url: Mapped[str | None] = mapped_column(Text)
    # call_analysis: {summary, call_summary, in_voicemail, user_sentiment,
    # call_successful, custom_analysis_data}
    call_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    call_cost: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    telephony_identifier: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Delivery bookkeeping (not exposed via API)
    livekit_room: Mapped[str | None] = mapped_column(String(128))
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)
    # Web calls only: room join token returned by create-web-call / get-call.
    access_token: Mapped[str | None] = mapped_column(Text)
    # Batch-call membership (not part of the Retell call object).
    batch_call_id: Mapped[str | None] = mapped_column(String(64), index=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(32))  # call_started|call_ended|call_analyzed
    url: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at_ms: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class BatchCall(Base):
    __tablename__ = "batch_calls"

    batch_call_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=new_batch_call_id
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    from_number: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(255))
    # Verbatim task list: [{"to_number": ..., "retell_llm_dynamic_variables": ...}, ...]
    tasks: Mapped[list[Any]] = mapped_column(JSON)
    trigger_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="sent")  # sent | scheduled
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    knowledge_base_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=new_knowledge_base_id
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    knowledge_base_name: Mapped[str] = mapped_column(String(255))
    # Sources are stored verbatim; retrieval/embedding is TODO (storage + CRUD only).
    # Each item: {"type": "text"|"url", "source_id": "src_...", ...}
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="complete")
    enable_auto_refresh: Mapped[bool] = mapped_column(Boolean, default=False)
    last_refreshed_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class KnowledgeBaseFile(Base):
    """Uploaded document blobs, kept out of KnowledgeBase.sources JSON so
    list/get endpoints never load file bytes."""

    __tablename__ = "knowledge_base_files"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.knowledge_base_id"), index=True
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class ConversationFlow(Base):
    __tablename__ = "conversation_flows"

    conversation_flow_id: Mapped[str] = mapped_column(
        String(128), primary_key=True, default=new_conversation_flow_id
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    global_prompt: Mapped[str | None] = mapped_column(Text)
    nodes: Mapped[list[Any] | None] = mapped_column(JSON)
    start_node_id: Mapped[str | None] = mapped_column(String(255))
    start_speaker: Mapped[str] = mapped_column(String(16), default="agent")
    model_choice: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tools: Mapped[list[Any] | None] = mapped_column(JSON)
    default_dynamic_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_modification_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)


class Contact(Base):
    """Dashboard-only resource (not part of the Retell API contract)."""

    __tablename__ = "contacts"

    contact_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_contact_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    phone_number: Mapped[str] = mapped_column(String(20), index=True)
    first_name: Mapped[str] = mapped_column(String(255), default="")
    last_name: Mapped[str] = mapped_column(String(255), default="")
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class Alert(Base):
    """Dashboard-only resource (not part of the Retell API contract)."""

    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_alert_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    check_every_min: Mapped[int] = mapped_column(Integer, default=5)
    lookback_min: Mapped[int] = mapped_column(Integer, default=60)
    metric: Mapped[str] = mapped_column(String(64))
    condition: Mapped[str] = mapped_column(String(32), default="above")
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    notify_emails: Mapped[list[Any]] = mapped_column(JSON, default=list)
    webhook_url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class QaCohort(Base):
    """Dashboard-only resource (not part of the Retell API contract)."""

    __tablename__ = "qa_cohorts"

    cohort_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_cohort_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    agents: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sampling_pct: Mapped[float] = mapped_column(Float, default=10.0)
    weekly_max: Mapped[int] = mapped_column(Integer, default=100)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_chat_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[int] = mapped_column(Integer, default=0)
    chat_status: Mapped[str] = mapped_column(String(24), default="ongoing")  # ongoing | ended
    # [{"message_id", "role": "agent"|"user", "content", "created_timestamp"}, ...]
    messages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    retell_llm_dynamic_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    start_timestamp: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    end_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)
