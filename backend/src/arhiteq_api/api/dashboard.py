"""Dashboard-only endpoints (NOT part of the Retell API contract).

These back the Arhiteq dashboard pages that Retell serves from its private
dashboard API: analytics, contacts, alerting, QA cohorts, API-key management,
webhook delivery log, and workspace settings. All additive — nothing here
changes the public Retell-compatible surface.
"""

import asyncio
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any, NamedTuple

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BeforeValidator, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import hash_key, require_api_key
from ..config import get_settings
from ..db import get_session
from ..ids import new_api_key, new_invite_token
from ..models import (
    DEFAULT_WEBHOOK_TIMEOUT_MS,
    DEFAULT_WORKSPACE_SETTINGS,
    Agent,
    AgentFolder,
    AgentVersion,
    Alert,
    ApiKey,
    BatchCall,
    Call,
    Chat,
    Contact,
    ConversationFlow,
    KnowledgeBase,
    KnowledgeBaseFile,
    PhoneNumber,
    QaCohort,
    RetellLLM,
    TestCaseBatchJob,
    TestCaseDefinition,
    TestCaseJob,
    WebhookDelivery,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    now_ms,
    workspace_settings,
)
from ..schemas import CompatModel, TestWebhookRequest
from ..services import webhooks
from ..services.gemini import build_genai_client, genai_credentials_available
from ..sessions import email_from_authorization
from ._deps import apply_patch, get_owned
from .auth_google import _email_allowed
from .concurrency import BASE_CONCURRENCY, CONCURRENCY_PURCHASE_LIMIT

router = APIRouter(tags=["dashboard"])

# Everything a workspace owns, ordered children-first so DELETE /workspace holds
# FK constraints without relying on database-level cascades. Every model with a
# workspace_id FK belongs here — a missing one makes the workspace undeletable
# on Postgres (SQLite won't notice), which
# test_every_workspace_scoped_table_is_deleted guards against.
WORKSPACE_CHILD_MODELS: tuple[type[Any], ...] = (
    PhoneNumber,  # references agents — must go before Agent
    Call,
    Chat,
    BatchCall,
    TestCaseJob,  # references batch jobs — must go before TestCaseBatchJob
    TestCaseBatchJob,
    TestCaseDefinition,
    KnowledgeBaseFile,
    KnowledgeBase,
    ConversationFlow,
    AgentVersion,  # references agents — must go before Agent
    Agent,
    RetellLLM,
    AgentFolder,
    Contact,
    Alert,
    QaCohort,
    WorkspaceInvite,
    WorkspaceMember,
    ApiKey,
)

DAY_MS = 86_400_000


# ------------------------------------------------------------- authorization


async def _assert_workspace_admin(
    authorization: str | None,
    api_key: ApiKey,
    session: AsyncSession,
    detail: str,
) -> str | None:
    """Require owner/admin for a session-authenticated caller.

    Returns the caller's email, or None when authenticated with a raw API
    key. A raw key carries no personal identity and is operator credentials,
    so it passes unrestricted (also the dev-mode path). A session JWT must
    belong to an owner or admin of the workspace.

    The allowlist carve-out applies only when there is no member row yet: an
    allowlisted login writes that row as owner, so a session issued before it
    exists must not be locked out. It must NOT override an existing row —
    `_email_allowed` matches whole domains, so with ARHITEQ_DASHBOARD_ALLOWED_
    DOMAINS set (the prod pattern) every employee invited as a `member` would
    otherwise read as owner and could mint a key to escalate.
    """
    email = email_from_authorization(authorization)
    if email is None:
        return None
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == api_key.workspace_id,
            WorkspaceMember.email == email,
        )
    )
    allowed = _email_allowed(email) if member is None else member.role in ("owner", "admin")
    if not allowed:
        raise HTTPException(403, detail=detail)
    return email


async def require_workspace_admin(
    authorization: str | None = Header(default=None),
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> ApiKey:
    """Gate API-key management to owners and admins.

    Minting a key is equivalent to becoming an operator — a raw key skips
    every role check below — so this must not be reachable by a role that is
    denied member management.
    """
    await _assert_workspace_admin(
        authorization,
        api_key,
        session,
        "Only workspace owners and admins can manage API keys",
    )
    return api_key


# ------------------------------------------------------------------ analytics


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _series(counts: Mapping[str, float], start_ms: int, days: int) -> list[dict[str, Any]]:
    """Dense day series over the window (charts need every day present)."""
    return [
        {"date": day, "value": counts.get(day, 0)}
        for day in (_day(start_ms + i * DAY_MS) for i in range(days))
    ]


def _breakdown(counter: Counter) -> list[dict[str, Any]]:
    return [{"name": name, "value": value} for name, value in counter.most_common()]


def _window(days: int, start_ms: int | None, end_ms: int | None) -> tuple[int, int, int]:
    """Resolve (start_ms, end_ms, days). Explicit range wins over `days`;
    the window is whole calendar days including the end day's bucket."""
    if start_ms is not None and end_ms is not None and end_ms >= start_ms:
        # Buckets are UTC days but explicit ranges arrive as local midnights,
        # and the query tolerates timestamps up to end_ms + DAY_MS — so span
        # every UTC day a returned call can land in, not just the raw delta.
        span_days = min(int((end_ms + DAY_MS) // DAY_MS - start_ms // DAY_MS) + 1, 365)
        return start_ms, end_ms, span_days
    days = max(1, min(days, 365))
    # Window is the last `days` calendar days *including today*, so the series
    # ends on today's bucket.
    return now_ms() - (days - 1) * DAY_MS, now_ms(), days


@router.get("/analytics/calls")
async def call_analytics(
    request: Request,
    days: int = 30,
    start_ms: int | None = None,
    end_ms: int | None = None,
    group_by: str | None = None,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    start_ms, end_ms, days = _window(days, start_ms, end_ms)
    agent_ids = request.query_params.getlist("agent_id")
    # Column-scoped: the aggregation below reads nine small fields, so selecting
    # whole Call rows would ship every transcript in the window to count them.
    query = select(
        Call.created_at_ms,
        Call.start_timestamp,
        Call.duration_ms,
        Call.call_analysis,
        Call.disconnection_reason,
        Call.direction,
        Call.agent_id,
        Call.agent_name,
        Call.latency,
    ).where(
        Call.workspace_id == api_key.workspace_id,
        Call.created_at_ms >= start_ms,
        Call.created_at_ms <= end_ms + DAY_MS,  # tolerate end-of-day timestamps
    )
    if agent_ids:
        query = query.where(Call.agent_id.in_(agent_ids))
    rows = (await session.execute(query)).all()

    durations = [c.duration_ms for c in rows if c.duration_ms]
    latencies: list[float] = []
    day_counts: Counter = Counter()
    day_minutes: Counter = Counter()
    successful: Counter = Counter()
    reasons: Counter = Counter()
    sentiments: Counter = Counter()
    directions: Counter = Counter()
    grouping = group_by if group_by in ("agent", "direction") else None
    group_counts: dict[str, Counter] = {}

    for c in rows:
        day = _day(c.start_timestamp or c.created_at_ms)
        day_counts[day] += 1
        if c.duration_ms:
            day_minutes[day] += c.duration_ms / 60_000
        analysis = c.call_analysis or {}
        if (ok := analysis.get("call_successful")) is not None:
            successful["Successful" if ok else "Unsuccessful"] += 1
        if c.disconnection_reason:
            reasons[c.disconnection_reason] += 1
        sentiments[analysis.get("user_sentiment") or "Unknown"] += 1
        if c.direction:
            directions[c.direction] += 1
        e2e = (c.latency or {}).get("e2e") or {}
        if isinstance(e2e, dict) and e2e.get("p50"):
            latencies.append(e2e["p50"])
        if grouping:
            # Per-group daily call-count series (small-multiples chart),
            # accumulated in the same pass rather than re-walking rows.
            name = (
                (c.agent_name or c.agent_id) if grouping == "agent" else (c.direction or "unknown")
            )
            group_counts.setdefault(name, Counter())[day] += 1

    out: dict[str, Any] = {
        "call_counts": len(rows),
        "avg_duration_s": round(sum(durations) / len(durations) / 1000, 1) if durations else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "call_counts_series": _series(day_counts, start_ms, days),
        "concurrency_series": _series(
            {d: round(m, 1) for d, m in day_minutes.items()}, start_ms, days
        ),
        "call_successful": _breakdown(successful),
        "disconnection_reason": _breakdown(reasons),
        "user_sentiment": _breakdown(sentiments),
        "phone_direction": _breakdown(directions),
    }

    if grouping:
        out["call_counts_groups"] = [
            {"name": name, "series": _series(counts, start_ms, days)}
            for name, counts in sorted(group_counts.items(), key=lambda kv: -sum(kv[1].values()))[
                :12
            ]
        ]
    return out


@router.get("/analytics/chats")
async def chat_analytics(
    request: Request,
    days: int = 30,
    start_ms: int | None = None,
    end_ms: int | None = None,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    start_ms, end_ms, days = _window(days, start_ms, end_ms)
    agent_ids = request.query_params.getlist("agent_id")
    query = select(Chat).where(
        Chat.workspace_id == api_key.workspace_id,
        Chat.created_at_ms >= start_ms,
        Chat.created_at_ms <= end_ms + DAY_MS,
    )
    if agent_ids:
        query = query.where(Chat.agent_id.in_(agent_ids))
    rows = (await session.scalars(query)).all()

    day_counts: Counter = Counter()
    day_messages: Counter = Counter()
    statuses: Counter = Counter()
    agents: Counter = Counter()
    message_totals: list[int] = []
    durations_ms: list[int] = []

    for c in rows:
        day = _day(c.start_timestamp or c.created_at_ms)
        day_counts[day] += 1
        n_messages = len(c.messages or [])
        day_messages[day] += n_messages
        message_totals.append(n_messages)
        statuses[c.chat_status] += 1
        agents[c.agent_id] += 1
        if c.end_timestamp and c.start_timestamp:
            durations_ms.append(c.end_timestamp - c.start_timestamp)

    return {
        "chat_counts": len(rows),
        "avg_messages": round(sum(message_totals) / len(message_totals), 1)
        if message_totals
        else 0,
        "avg_duration_s": round(sum(durations_ms) / len(durations_ms) / 1000, 1)
        if durations_ms
        else 0,
        "chat_counts_series": _series(day_counts, start_ms, days),
        "messages_series": _series(day_messages, start_ms, days),
        "chat_status": _breakdown(statuses),
        "chat_agent": _breakdown(agents),
    }


class CallInsightsRequest(CompatModel):
    days: int = 7
    agent_id: list[str] = Field(default_factory=list)
    limit: int = Field(default=200, ge=1, le=500)


_INSIGHTS_PROMPT = """\
You are an analyst for a voice-AI call platform. Below is a sample of recent
calls (one per line: start time, agent, direction, duration, status,
disconnection reason, user sentiment, successful flag, then the call summary).

Write a concise insights report in markdown with three short sections:
**Trends**, **Problems**, and **Recommendations**. Ground every claim in the
data; quote counts/percentages you can actually derive. No preamble.

Calls:
{lines}"""


@router.post("/analytics/call-insights")
async def call_insights(
    body: CallInsightsRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """AI summary of recent calls (Call History's ✨ button). Real Gemini run
    over real call rows — 422s when no GenAI credentials are configured."""
    settings = get_settings()
    if not genai_credentials_available(settings):
        raise HTTPException(422, detail="AI insights need Google GenAI credentials configured")

    days = max(1, min(body.days, 90))
    query = (
        select(Call)
        .where(
            Call.workspace_id == api_key.workspace_id,
            Call.created_at_ms >= now_ms() - days * DAY_MS,
        )
        .order_by(Call.created_at_ms.desc())
        .limit(body.limit)
    )
    if body.agent_id:
        query = query.where(Call.agent_id.in_(body.agent_id))
    rows = (await session.scalars(query)).all()
    if not rows:
        raise HTTPException(422, detail="No calls in the selected window to analyze")

    def line(c: Call) -> str:
        analysis = c.call_analysis or {}
        started = datetime.fromtimestamp(
            (c.start_timestamp or c.created_at_ms) / 1000, tz=UTC
        ).strftime("%Y-%m-%d %H:%M")
        summary = (analysis.get("call_summary") or analysis.get("summary") or "").replace(
            "\n", " "
        )[:200]
        duration_s = round((c.duration_ms or 0) / 1000)
        return (
            f"{started} | {c.agent_name or c.agent_id} | {c.direction or '-'} | {duration_s}s"
            f" | {c.call_status} | {c.disconnection_reason or '-'}"
            f" | {analysis.get('user_sentiment') or '-'} | {analysis.get('call_successful')}"
            f" | {summary}"
        )

    prompt = _INSIGHTS_PROMPT.format(lines="\n".join(line(c) for c in rows))
    try:
        client = build_genai_client(settings)
        resp = await client.aio.models.generate_content(
            model=settings.analysis_model, contents=prompt, config={"temperature": 0.2}
        )
        text = (resp.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — surface the provider failure
        raise HTTPException(502, detail=f"Insights generation failed: {exc}") from None
    if not text:
        raise HTTPException(502, detail="Insights generation returned an empty response")
    return {"insights": text, "calls_analyzed": len(rows), "window_days": days}


# -------------------------------------------------------------- agent folders


def _folder_to_dict(f: AgentFolder) -> dict[str, Any]:
    return {
        "folder_id": f.folder_id,
        "folder_name": f.folder_name,
        "last_modification_timestamp": f.last_modification_timestamp,
    }


class FolderRequest(CompatModel):
    folder_name: str = Field(min_length=1, max_length=255)

    @field_validator("folder_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("folder_name must not be blank")
        return v


async def _reject_duplicate_folder_name(
    session: AsyncSession, workspace_id: str, folder_name: str, exclude_folder_id: str | None = None
) -> None:
    query = select(AgentFolder.folder_id).where(
        AgentFolder.workspace_id == workspace_id,
        func.lower(AgentFolder.folder_name) == folder_name.lower(),
    )
    if exclude_folder_id is not None:
        query = query.where(AgentFolder.folder_id != exclude_folder_id)
    if (await session.scalars(query)).first() is not None:
        raise HTTPException(409, detail="A folder with this name already exists")


@router.get("/list-agent-folders")
async def list_agent_folders(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    folders = (
        await session.scalars(
            select(AgentFolder)
            .where(AgentFolder.workspace_id == api_key.workspace_id)
            .order_by(AgentFolder.folder_name)
        )
    ).all()
    return [_folder_to_dict(f) for f in folders]


@router.post("/create-agent-folder", status_code=201)
async def create_agent_folder(
    body: FolderRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    await _reject_duplicate_folder_name(session, api_key.workspace_id, body.folder_name)
    folder = AgentFolder(workspace_id=api_key.workspace_id, folder_name=body.folder_name)
    session.add(folder)
    await session.commit()
    return _folder_to_dict(folder)


@router.patch("/update-agent-folder/{folder_id}")
async def update_agent_folder(
    folder_id: str,
    body: FolderRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    folder = await get_owned(
        session, AgentFolder, folder_id, api_key.workspace_id, detail="Folder not found"
    )
    await _reject_duplicate_folder_name(
        session, api_key.workspace_id, body.folder_name, exclude_folder_id=folder_id
    )
    folder.folder_name = body.folder_name
    folder.last_modification_timestamp = now_ms()
    await session.commit()
    return _folder_to_dict(folder)


@router.delete("/delete-agent-folder/{folder_id}", status_code=204)
async def delete_agent_folder(
    folder_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    folder = await get_owned(
        session, AgentFolder, folder_id, api_key.workspace_id, detail="Folder not found"
    )
    # Agents fall back to "no folder" — deleting a folder never deletes agents.
    # Unscoped on purpose: folder ids are globally unique PKs, and this also
    # heals any legacy dangling reference from before folder_id validation.
    await session.execute(update(Agent).where(Agent.folder_id == folder_id).values(folder_id=None))
    await session.delete(folder)
    await session.commit()


# ------------------------------------------------------------------- contacts


def _contact_to_dict(c: Contact, related: int = 0, latest: int | None = None) -> dict[str, Any]:
    return {
        "contact_id": c.contact_id,
        "phone_number": c.phone_number,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "timezone": c.timezone,
        "do_not_call": c.do_not_call,
        "external_id": c.external_id,
        "custom_fields": c.custom_fields or {},
        "related_conversations": related,
        "latest_conversation": latest,
    }


class CreateContactRequest(CompatModel):
    phone_number: str
    first_name: str = ""
    last_name: str = ""
    timezone: str | None = None
    do_not_call: bool = False
    external_id: str | None = None
    custom_fields: dict[str, Any] | None = None


_CONTACT_MUTABLE_FIELDS = set(CreateContactRequest.model_fields)


@router.get("/list-contacts")
async def list_contacts(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    contacts = (
        await session.scalars(select(Contact).where(Contact.workspace_id == api_key.workspace_id))
    ).all()
    # Conversation stats come from the calls table, matched on either leg.
    # Aggregated DB-side (one row per number, not per call), and restricted to
    # the numbers actually on screen so the scan doesn't grow with total
    # workspace call volume forever.
    numbers = [c.phone_number for c in contacts if c.phone_number]
    stats: dict[str, tuple[int, int]] = {}
    for number_col in (Call.to_number, Call.from_number):
        if not numbers:
            break
        result = await session.execute(
            select(number_col, func.count(), func.max(Call.created_at_ms))
            .where(Call.workspace_id == api_key.workspace_id, number_col.in_(numbers))
            .group_by(number_col)
        )
        for number, count, latest in result:
            prev = stats.get(number, (0, 0))
            stats[number] = (prev[0] + count, max(prev[1], latest or 0))
    return [
        _contact_to_dict(
            c,
            related=stats.get(c.phone_number, (0, 0))[0],
            latest=stats.get(c.phone_number, (0, 0))[1] or None,
        )
        for c in contacts
    ]


@router.post("/create-contact", status_code=201)
async def create_contact(
    body: CreateContactRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    contact = Contact(workspace_id=api_key.workspace_id, **body.model_dump(exclude_none=True))
    session.add(contact)
    await session.commit()
    return _contact_to_dict(contact)


@router.patch("/update-contact/{contact_id}")
async def update_contact(
    contact_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    contact = await get_owned(
        session, Contact, contact_id, api_key.workspace_id, detail="Contact not found"
    )
    payload = await request.json()
    apply_patch(contact, payload, _CONTACT_MUTABLE_FIELDS)
    await session.commit()
    return _contact_to_dict(contact)


@router.delete("/delete-contact/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    contact = await get_owned(
        session, Contact, contact_id, api_key.workspace_id, detail="Contact not found"
    )
    await session.delete(contact)
    await session.commit()


# --------------------------------------------------------------------- alerts


def _alert_to_dict(a: Alert) -> dict[str, Any]:
    return {
        "alert_id": a.alert_id,
        "name": a.name,
        "check_every_min": a.check_every_min,
        "lookback_min": a.lookback_min,
        "metric": a.metric,
        "condition": a.condition,
        "threshold": a.threshold,
        "compare_to": a.compare_to,
        "notify_emails": a.notify_emails or [],
        "webhook_url": a.webhook_url,
        "enabled": a.enabled,
    }


class CreateAlertRequest(CompatModel):
    name: str
    metric: str
    condition: str = "above"
    threshold: float = 0.0
    compare_to: str = Field(default="value", pattern="^(value|last_cycle)$")
    check_every_min: int = 5
    lookback_min: int = 60
    notify_emails: list[str] = Field(default_factory=list)
    webhook_url: str | None = None
    enabled: bool = True


class UpdateAlertRequest(CompatModel):
    """PATCH body: same constraints as create, everything optional."""

    name: str | None = None
    metric: str | None = None
    condition: str | None = None
    threshold: float | None = None
    compare_to: str | None = Field(default=None, pattern="^(value|last_cycle)$")
    check_every_min: int | None = None
    lookback_min: int | None = None
    notify_emails: list[str] | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


# Derived, so a new alert field can't be added to the model and silently
# no-op on PATCH. Still excludes the extra="allow" passthrough keys, which is
# what the allowlist is for.
_ALERT_FIELDS = set(UpdateAlertRequest.model_fields)


@router.get("/list-alerts")
async def list_alerts(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(select(Alert).where(Alert.workspace_id == api_key.workspace_id))
    ).all()
    return [_alert_to_dict(a) for a in rows]


@router.post("/create-alert", status_code=201)
async def create_alert(
    body: CreateAlertRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    alert = Alert(workspace_id=api_key.workspace_id, **body.model_dump())
    session.add(alert)
    await session.commit()
    return _alert_to_dict(alert)


@router.patch("/update-alert/{alert_id}")
async def update_alert(
    alert_id: str,
    body: UpdateAlertRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    alert = await get_owned(
        session, Alert, alert_id, api_key.workspace_id, detail="Alert not found"
    )
    payload = body.model_dump(exclude_unset=True)
    # Only webhook_url is nullable; null on the others means "no change".
    apply_patch(
        alert,
        {k: v for k, v in payload.items() if v is not None or k == "webhook_url"},
        _ALERT_FIELDS,
    )
    await session.commit()
    return _alert_to_dict(alert)


@router.delete("/delete-alert/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    alert = await get_owned(
        session, Alert, alert_id, api_key.workspace_id, detail="Alert not found"
    )
    await session.delete(alert)
    await session.commit()


# ----------------------------------------------------------------- QA cohorts


QA_WINDOW_DAYS = 30


def _sample_bucket(call_id: str) -> int:
    """Deterministic 0-99 bucket for sampling_pct comparisons."""
    return int(sha256(call_id.encode()).hexdigest()[:8], 16) % 100


def _cohort_metrics(
    cohort: QaCohort, calls: Sequence[Any], buckets: Mapping[str, int] | None = None
) -> dict[str, Any]:
    """Score a cohort over its sampled calls (last QA_WINDOW_DAYS).

    Sampling is deterministic per call (hash of call_id vs sampling_pct) so
    repeated page loads score the same sample, capped at ~a month of the
    cohort's weekly_max. `buckets` lets a caller scoring many cohorts over one
    window hash each call_id once instead of once per cohort.
    """
    agent_set = set(cohort.agents or [])
    matching = [
        c
        for c in calls
        if (not agent_set or c.agent_id in agent_set)
        and (not cohort.min_duration_s or (c.duration_ms or 0) >= cohort.min_duration_s * 1000)
    ]
    pct = max(0.0, min(cohort.sampling_pct or 0.0, 100.0))
    # Stable digest, not hash(): PYTHONHASHSEED would resample per process.
    sampled = [
        c
        for c in matching
        if (buckets[c.call_id] if buckets is not None else _sample_bucket(c.call_id)) < pct
    ]
    # weekly_max is non-null (default 100); 0 legitimately means "score
    # nothing", so no falsy-or fallback here.
    cap = cohort.weekly_max * 4
    sampled = sampled[:cap]

    analyzed = [c for c in sampled if (c.call_analysis or {}).get("call_successful") is not None]
    successful = sum(1 for c in analyzed if (c.call_analysis or {}).get("call_successful"))
    transferred = [c for c in sampled if "transfer" in (c.disconnection_reason or "").lower()]
    transfer_rate = round(100 * len(transferred) / len(sampled)) if sampled else 0
    success_rate = round(100 * successful / len(analyzed)) if analyzed else 0
    # Approximation: a transferred call ends at the transfer, so its duration
    # is the time the caller waited to reach a human.
    wait_times = [c.duration_ms for c in transferred if c.duration_ms]
    return {
        "sample_size": len(sampled),
        "success_rate": success_rate,
        "transfer_success_rate": transfer_rate,
        "transfer_wait_time_s": round(sum(wait_times) / len(wait_times) / 1000, 1)
        if wait_times
        else 0,
        "score": transfer_rate if cohort.scoring_metric == "transfer" else success_rate,
    }


def _cohort_to_dict(c: QaCohort, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "cohort_id": c.cohort_id,
        "name": c.name,
        "agents": c.agents or [],
        "sampling_pct": c.sampling_pct,
        "weekly_max": c.weekly_max,
        "min_duration_s": c.min_duration_s,
        "success_criteria": c.success_criteria,
        "scoring_metric": c.scoring_metric or "call_successful",
        # Scoring an empty window yields exactly the all-zero metric block, so
        # create-qa-cohort and list-qa-cohorts can't return different shapes.
        **(metrics if metrics is not None else _cohort_metrics(c, [])),
    }


class CreateCohortRequest(CompatModel):
    name: str
    agents: list[str] = Field(default_factory=list)
    sampling_pct: float = 10.0
    weekly_max: int = Field(default=100, ge=0)
    min_duration_s: int | None = Field(default=None, ge=0, le=86_400)
    success_criteria: str | None = None
    scoring_metric: str = Field(default="call_successful", pattern="^(call_successful|transfer)$")


@router.get("/list-qa-cohorts")
async def list_qa_cohorts(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(select(QaCohort).where(QaCohort.workspace_id == api_key.workspace_id))
    ).all()
    if not rows:
        return []
    # One shared window of ended calls; per-cohort filters run in memory.
    # Column-scoped: scoring reads six small fields, so hydrating full Call rows
    # would ship every transcript in the window for nothing.
    calls = (
        await session.execute(
            select(
                Call.call_id,
                Call.agent_id,
                Call.duration_ms,
                Call.call_analysis,
                Call.disconnection_reason,
                Call.created_at_ms,
            )
            .where(
                Call.workspace_id == api_key.workspace_id,
                Call.created_at_ms >= now_ms() - QA_WINDOW_DAYS * DAY_MS,
                Call.call_status.in_(("ended", "error", "not_connected")),
            )
            # Deterministic order so the weekly_max cap truncates the same
            # (most recent) sample on every page load.
            .order_by(Call.created_at_ms.desc(), Call.call_id)
        )
    ).all()
    # The bucket depends only on call_id, so hash once for all cohorts.
    buckets = {c.call_id: _sample_bucket(c.call_id) for c in calls}
    return [_cohort_to_dict(c, _cohort_metrics(c, calls, buckets)) for c in rows]


@router.post("/create-qa-cohort", status_code=201)
async def create_qa_cohort(
    body: CreateCohortRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    cohort = QaCohort(workspace_id=api_key.workspace_id, **body.model_dump())
    session.add(cohort)
    await session.commit()
    return _cohort_to_dict(cohort)


@router.delete("/delete-qa-cohort/{cohort_id}", status_code=204)
async def delete_qa_cohort(
    cohort_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    cohort = await get_owned(
        session, QaCohort, cohort_id, api_key.workspace_id, detail="Cohort not found"
    )
    await session.delete(cohort)
    await session.commit()


# --------------------------------------------------------- batch call drafts


def _draft_to_dict(b: BatchCall) -> dict[str, Any]:
    return {
        "batch_call_id": b.batch_call_id,
        "name": b.name,
        "from_number": b.from_number,
        "tasks": b.tasks or [],
        "trigger_timestamp": b.trigger_timestamp,
        "reserved_concurrency": b.reserved_concurrency,
        "call_time_window": b.call_time_window,
        "created_at_ms": b.created_at_ms,
    }


class SaveBatchCallDraftRequest(CompatModel):
    from_number: str | None = None
    name: str | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    trigger_timestamp: int | None = None
    reserved_concurrency: int | None = Field(default=None, ge=0, le=500)
    call_time_window: dict[str, Any] | None = None


@router.post("/save-batch-call-draft", status_code=201)
async def save_batch_call_draft(
    body: SaveBatchCallDraftRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Dashboard "Save as draft": a BatchCall row that never dials. Unlike
    create-batch-call, an incomplete form (no recipients yet) is fine."""
    draft = BatchCall(
        workspace_id=api_key.workspace_id,
        from_number=body.from_number or "",
        name=body.name,
        tasks=body.tasks,
        trigger_timestamp=body.trigger_timestamp,
        reserved_concurrency=body.reserved_concurrency,
        call_time_window=body.call_time_window,
        status="draft",
    )
    session.add(draft)
    await session.commit()
    return _draft_to_dict(draft)


@router.get("/list-batch-call-drafts")
async def list_batch_call_drafts(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(BatchCall)
            .where(BatchCall.workspace_id == api_key.workspace_id, BatchCall.status == "draft")
            .order_by(BatchCall.created_at_ms.desc())
        )
    ).all()
    return [_draft_to_dict(b) for b in rows]


@router.delete("/delete-batch-call-draft/{batch_call_id}", status_code=204)
async def delete_batch_call_draft(
    batch_call_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    draft = await get_owned(
        session, BatchCall, batch_call_id, api_key.workspace_id, detail="Draft not found"
    )
    # A sent batch is no longer a draft, and stays indistinguishable from a
    # missing one here.
    if draft.status != "draft":
        raise HTTPException(404, detail="Draft not found")
    await session.delete(draft)
    await session.commit()


# ------------------------------------------------------------------- API keys


def _api_key_to_dict(k: ApiKey, secret: str | None = None) -> dict[str, Any]:
    out = {
        "key_id": str(k.id),
        "name": k.name,
        "prefix": f"{k.key_material[:8]}…",
        "created_at": k.created_at_ms,
        "revoked": k.revoked,
    }
    if secret is not None:
        out["secret"] = secret  # full key, shown exactly once at creation
    return out


class CreateApiKeyRequest(CompatModel):
    name: str = "API key"


@router.get("/list-api-keys")
async def list_api_keys(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(ApiKey)
            .where(ApiKey.workspace_id == api_key.workspace_id)
            .order_by(ApiKey.created_at_ms.desc())
        )
    ).all()
    return [_api_key_to_dict(k) for k in rows]


@router.post("/create-api-key", status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    api_key: ApiKey = Depends(require_workspace_admin),
    session: AsyncSession = Depends(get_session),
):
    secret = new_api_key()
    row = ApiKey(
        workspace_id=api_key.workspace_id,
        key_hash=hash_key(secret),
        key_material=secret,
        name=body.name,
    )
    session.add(row)
    await session.commit()
    return _api_key_to_dict(row, secret=secret)


@router.post("/revoke-api-key/{key_id}")
async def revoke_api_key(
    key_id: int,
    api_key: ApiKey = Depends(require_workspace_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await get_owned(session, ApiKey, key_id, api_key.workspace_id, detail="API key not found")
    row.revoked = True
    await session.commit()
    return _api_key_to_dict(row)


# ---------------------------------------------------- webhook delivery log


@router.get("/list-webhook-deliveries")
async def list_webhook_deliveries(
    limit: int = 100,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(WebhookDelivery)
                .join(Call, Call.call_id == WebhookDelivery.call_id)
                .where(Call.workspace_id == api_key.workspace_id)
                .order_by(WebhookDelivery.created_at_ms.desc())
                .limit(max(1, min(limit, 500)))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "delivery_id": str(d.id),
            "event": d.event,
            "status": d.last_status_code or 0,
            "timestamp": d.created_at_ms,
            "duration_ms": 0,
            "url": d.url,
            "attempts": d.attempts,
            "delivered": d.delivered,
        }
        for d in rows
    ]


# ------------------------------------------------------------------ workspace


def _workspace_json(ws: Workspace) -> dict[str, Any]:
    return {
        "workspace_id": ws.id,
        "name": ws.name,
        "webhook_url": ws.webhook_url,
        "settings": workspace_settings(ws),
    }


def _require_int(value: Any, field: str, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (lo <= value <= hi):
        raise HTTPException(422, detail=f"{field} must be an integer between {lo} and {hi}")
    return value


def _validate_contact_fields(value: Any) -> list[dict[str, str]]:
    """Normalize the custom contact-field definitions (Settings → Contacts)."""
    if not isinstance(value, list) or len(value) > 50:
        raise HTTPException(422, detail="contact_field_definitions must be a list (max 50)")
    seen_keys: set[str] = set()
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or not re.match(r"^[a-z][a-z0-9_]{0,63}$", item["key"])
            or item.get("type") not in ("string", "number", "boolean", "date")
            or not isinstance(item.get("label"), str)
            or not item["label"].strip()
        ):
            raise HTTPException(
                422,
                detail=(
                    "each contact field needs a snake_case key, a label, and a "
                    "type of string|number|boolean|date"
                ),
            )
        if item["key"] in seen_keys:
            raise HTTPException(422, detail=f"duplicate field key {item['key']!r}")
        seen_keys.add(item["key"])
    return [{"key": i["key"], "label": i["label"].strip(), "type": i["type"]} for i in value]


def _validate_cps_limits(stored: dict[str, Any], value: Any) -> dict[str, int]:
    """Merge a per-provider calls-per-second patch over the stored limits."""
    if not isinstance(value, dict):
        raise HTTPException(422, detail="cps_limits must be an object")
    limits = dict(stored.get("cps_limits") or {})
    for provider, cps in value.items():
        if provider not in DEFAULT_WORKSPACE_SETTINGS["cps_limits"]:
            raise HTTPException(422, detail=f"Unknown cps provider {provider!r}")
        limits[provider] = _require_int(cps, f"cps_limits.{provider}", 1, 100)
    return limits


def _validate_billing_email(value: Any) -> str | None:
    if value is not None and (not isinstance(value, str) or not re.match(_EMAIL_RE, value)):
        raise HTTPException(422, detail="billing_email must be a valid email")
    return _norm_email(value) if isinstance(value, str) else None


def _merged_settings_patch(stored: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Validate a partial settings update and merge it over the stored dict.

    Unknown keys are rejected (they'd silently do nothing), value ranges are
    pinned so the Limits page can't write a broken concurrency config.
    """
    out = dict(stored)
    for key, value in patch.items():
        match key:
            case "billing_email":
                out[key] = _validate_billing_email(value)
            case "purchased_concurrency":
                out[key] = _require_int(value, key, 0, CONCURRENCY_PURCHASE_LIMIT)
            case "reserved_inbound_concurrency":
                out[key] = _require_int(
                    value, key, 0, BASE_CONCURRENCY + CONCURRENCY_PURCHASE_LIMIT
                )
            case "llm_token_limit":
                out[key] = _require_int(value, key, 1024, 131_072)
            case (
                "concurrency_burst_enabled"
                | "llm_failover_enabled"
                | "auto_call_retry_enabled"
                | "conductor_messages_enabled"
            ):
                if not isinstance(value, bool):
                    raise HTTPException(422, detail=f"{key} must be a boolean")
                out[key] = value
            case "contact_field_definitions":
                out[key] = _validate_contact_fields(value)
            case "cps_limits":
                out[key] = _validate_cps_limits(stored, value)
            case _:
                raise HTTPException(422, detail=f"Unknown setting {key!r}")

    # Reserving the workspace's entire capacity would silently deadlock every
    # outbound and web call (effective outbound limit 0), so at least one
    # non-reserved slot must remain. `out` starts as a copy of `stored`, so it
    # already carries the unpatched values.
    limit = BASE_CONCURRENCY + int(out.get("purchased_concurrency", 0) or 0)
    reserved = int(out.get("reserved_inbound_concurrency", 0) or 0)
    if reserved >= limit:
        raise HTTPException(
            422,
            detail=(
                f"reserved_inbound_concurrency must stay below the concurrency limit ({limit}) "
                "so outbound calls keep at least one slot"
            ),
        )
    return out


@router.get("/workspace")
async def get_workspace(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    ws = await session.get(Workspace, api_key.workspace_id)
    if ws is None:
        raise HTTPException(404, detail="Workspace not found")
    return _workspace_json(ws)


@router.patch("/workspace")
async def update_workspace(
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    ws = await session.get(Workspace, api_key.workspace_id)
    if ws is None:
        raise HTTPException(404, detail="Workspace not found")
    payload = await request.json()
    apply_patch(ws, payload, {"name", "webhook_url"})
    if "settings" in payload:
        if not isinstance(payload["settings"], dict):
            raise HTTPException(422, detail="settings must be an object")
        ws.settings = _merged_settings_patch(ws.settings or {}, payload["settings"])
    await session.commit()
    return _workspace_json(ws)


@router.post("/test-workspace-webhook")
async def test_workspace_webhook(
    body: TestWebhookRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Send one signed sample event to the workspace webhook URL (or the URL in
    the request — the on-screen, possibly-unsaved value) and report back.

    Same contract as /test-agent-webhook, minus the agent: powers the "Test"
    buttons on Settings → Webhooks and the alert modal.
    """
    url = (body.webhook_url or "").strip()
    if not url:
        ws = await session.get(Workspace, api_key.workspace_id)
        url = (ws.webhook_url or "").strip() if ws else ""
    if not url:
        raise HTTPException(422, detail="No webhook URL configured to test")
    return await webhooks.send_test_event(
        session,
        api_key.workspace_id,
        url=url,
        event=body.event,
        call=webhooks.sample_call(api_key.workspace_id),
        timeout_ms=body.webhook_timeout_ms or DEFAULT_WEBHOOK_TIMEOUT_MS,
    )


# -------------------------------------------------------------- system status


def _component(key: str, name: str, status: str, detail: str = "") -> dict[str, Any]:
    return {"key": key, "name": name, "status": status, "detail": detail}


def _configured(key: str, name: str, ok: bool, missing_detail: str) -> dict[str, Any]:
    """A component whose health is just "is it configured" — status and detail
    stay paired so one can't be flipped without the other."""
    return _component(
        key, name, "operational" if ok else "not_configured", "" if ok else missing_detail
    )


async def _probe_livekit(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        # LiveKit answers its root with 200 "OK"; any HTTP answer proves the
        # media server is up — 5xx means reachable-but-unhealthy.
        status = "operational" if resp.status_code < 500 else "degraded"
        return _component("livekit", "Voice infrastructure (LiveKit)", status)
    except Exception:  # noqa: BLE001 — unreachable, not an app error
        return _component("livekit", "Voice infrastructure (LiveKit)", "down", "Unreachable")


@router.get("/system-status")
async def system_status(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Live component health for Settings → Reliability.

    Real checks, not a hardcoded green wall: DB round-trip, LiveKit HTTP
    reachability, credential presence for the LLM, telephony config, and the
    webhook-delivery failure backlog for this workspace.
    """
    settings = get_settings()
    lk_url = settings.livekit_url.replace("wss://", "https://").replace("ws://", "http://")
    # Start the probe first and await it last: it's the slow check (up to 3s
    # when LiveKit is down, exactly when an operator loads this page) and it
    # shares nothing with the DB queries.
    probe = asyncio.create_task(_probe_livekit(lk_url))

    components: list[dict[str, Any]] = [_component("api", "API", "operational")]

    try:
        await session.execute(select(func.count()).select_from(Workspace).limit(1))
        components.append(_component("database", "Database", "operational"))
    except Exception as exc:  # noqa: BLE001 — any DB failure is the finding
        components.append(_component("database", "Database", "down", str(exc)[:200]))

    components.append(await probe)
    components.append(
        _configured(
            "telephony",
            "Telephony (SIP)",
            bool(settings.sip_outbound_trunk_id),
            "No outbound SIP trunk configured",
        )
    )
    components.append(
        _configured(
            "llm",
            "LLM (Gemini)",
            genai_credentials_available(settings),
            "No Google GenAI credentials",
        )
    )

    # Webhooks: failures pending retry (or exhausted) in the last 24h.
    day_ago = now_ms() - DAY_MS
    failed = (
        await session.scalar(
            select(func.count())
            .select_from(WebhookDelivery)
            .join(Call, Call.call_id == WebhookDelivery.call_id)
            .where(
                Call.workspace_id == api_key.workspace_id,
                WebhookDelivery.delivered.is_(False),
                WebhookDelivery.attempts > 0,
                WebhookDelivery.created_at_ms >= day_ago,
            )
        )
        or 0
    )
    components.append(
        _component(
            "webhooks",
            "Webhook delivery",
            "operational" if failed == 0 else "degraded",
            "" if failed == 0 else f"{failed} failed deliveries in the last 24h",
        )
    )

    return {"checked_at_ms": now_ms(), "components": components}


# --------------------------------------------------------- members & invites

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _norm_email(v: Any) -> Any:
    """Members and invites store emails lowercase; normalize before the
    pattern check so validation sees the stored form."""
    return v.strip().lower() if isinstance(v, str) else v


NormalizedEmail = Annotated[
    str, BeforeValidator(_norm_email), Field(min_length=3, max_length=320, pattern=_EMAIL_RE)
]


def _member_json(m: WorkspaceMember) -> dict[str, Any]:
    return {"email": m.email, "name": m.name, "role": m.role, "created_at_ms": m.created_at_ms}


def _invite_json(inv: WorkspaceInvite) -> dict[str, Any]:
    return {
        "invite_id": inv.id,
        "email": inv.email,
        "role": inv.role,
        "status": inv.status,
        "token": inv.token,
        "invited_by": inv.invited_by,
        "created_at_ms": inv.created_at_ms,
        "expires_at_ms": inv.expires_at_ms,
    }


class MemberManager(NamedTuple):
    api_key: ApiKey
    email: str | None  # None when authenticated with a raw API key


async def require_member_manager(
    authorization: str | None = Header(default=None),
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> MemberManager:
    """Gate member/invite management to owners and admins.

    Same rule as `require_workspace_admin`, but carries the caller's email
    through so `remove-member` can refuse self-removal.
    """
    email = await _assert_workspace_admin(
        authorization,
        api_key,
        session,
        "Only workspace owners and admins can manage members",
    )
    return MemberManager(api_key, email)


@router.get("/list-members")
async def list_members(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == api_key.workspace_id)
            .order_by(WorkspaceMember.created_at_ms)
        )
    ).all()
    return [_member_json(m) for m in rows]


@router.get("/list-invites")
async def list_invites(
    manager: MemberManager = Depends(require_member_manager),
    session: AsyncSession = Depends(get_session),
):
    # Manager-only: the response carries live invite tokens. Expiry is lazy
    # (nothing flips status), so filter expired rows here — re-inviting the
    # same email regenerates the hidden row rather than conflicting with it.
    rows = (
        await session.scalars(
            select(WorkspaceInvite)
            .where(
                WorkspaceInvite.workspace_id == manager.api_key.workspace_id,
                WorkspaceInvite.status == "pending",
                WorkspaceInvite.expires_at_ms > now_ms(),
            )
            .order_by(WorkspaceInvite.created_at_ms)
        )
    ).all()
    return [_invite_json(inv) for inv in rows]


class CreateInviteRequest(CompatModel):
    email: NormalizedEmail
    role: str = Field(default="member", pattern="^(admin|member)$")


@router.post("/create-invite", status_code=201)
async def create_invite(
    body: CreateInviteRequest,
    manager: MemberManager = Depends(require_member_manager),
    session: AsyncSession = Depends(get_session),
):
    email = body.email
    workspace_id = manager.api_key.workspace_id
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.email == email,
        )
    )
    if member is not None:
        raise HTTPException(409, detail=f"{email} is already a member of this workspace")

    expires_at_ms = now_ms() + get_settings().invite_ttl_hours * 3_600_000

    # Re-inviting an email with a live invite regenerates that row (fresh
    # token + expiry) instead of stacking rows. Losing the partial-unique-
    # index race to a concurrent create lands on the update path on retry.
    for attempt in (1, 2):
        invite = await session.scalar(
            select(WorkspaceInvite).where(
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.email == email,
                WorkspaceInvite.status == "pending",
            )
        )
        if invite is not None:
            invite.token = new_invite_token()
            invite.role = body.role
            invite.expires_at_ms = expires_at_ms
            invite.invited_by = manager.email or invite.invited_by
        else:
            invite = WorkspaceInvite(
                workspace_id=workspace_id,
                email=email,
                role=body.role,
                invited_by=manager.email,
                expires_at_ms=expires_at_ms,
            )
            session.add(invite)
        try:
            await session.commit()
            return _invite_json(invite)
        except IntegrityError:
            await session.rollback()
            if attempt == 2:
                raise HTTPException(409, detail="Invite is being modified concurrently") from None


@router.post("/revoke-invite/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: str,
    manager: MemberManager = Depends(require_member_manager),
    session: AsyncSession = Depends(get_session),
):
    invite = await get_owned(
        session,
        WorkspaceInvite,
        invite_id,
        manager.api_key.workspace_id,
        detail="Invite not found",
    )
    if invite.status != "pending":
        raise HTTPException(409, detail=f"Invite is already {invite.status}")
    invite.status = "revoked"
    await session.commit()


class RemoveMemberRequest(CompatModel):
    # No pattern check here on purpose: removing a member that was never a
    # valid email should 404, not 422.
    email: Annotated[str, BeforeValidator(_norm_email), Field(min_length=3, max_length=320)]


@router.post("/remove-member", status_code=204)
async def remove_member(
    body: RemoveMemberRequest,
    manager: MemberManager = Depends(require_member_manager),
    session: AsyncSession = Depends(get_session),
):
    """Offboarding: membership grants login, so removing the row (plus the
    allowlist entry, for allowlisted emails) is what actually revokes access.
    Existing sessions expire on their own TTL."""
    if manager.email is not None and manager.email == body.email:
        raise HTTPException(403, detail="You can't remove yourself from the workspace")
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == manager.api_key.workspace_id,
            WorkspaceMember.email == body.email,
        )
    )
    if member is None:
        raise HTTPException(404, detail="Member not found")
    await session.delete(member)
    await session.commit()


# -------------------------------------------------------- workspace deletion


@router.delete("/workspace", status_code=204)
async def delete_workspace(
    manager: MemberManager = Depends(require_member_manager),
    session: AsyncSession = Depends(get_session),
):
    """Danger zone: delete the workspace and everything in it.

    Gated to owners/admins (or operator API keys) by require_member_manager.
    Row deletes are ordered children-first so FK constraints hold without
    relying on database-level cascades.
    """
    workspace_id = manager.api_key.workspace_id
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, detail="Workspace not found")

    call_ids = select(Call.call_id).where(Call.workspace_id == workspace_id)
    await session.execute(
        WebhookDelivery.__table__.delete().where(WebhookDelivery.call_id.in_(call_ids))
    )
    for model in WORKSPACE_CHILD_MODELS:
        await session.execute(
            model.__table__.delete().where(model.__table__.c.workspace_id == workspace_id)
        )
    await session.delete(ws)
    await session.commit()
