"""Dashboard-only endpoints (NOT part of the Retell API contract).

These back the Architeq dashboard pages that Retell serves from its private
dashboard API: analytics, contacts, alerting, QA cohorts, API-key management,
webhook delivery log, and workspace settings. All additive — nothing here
changes the public Retell-compatible surface.
"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import hash_key, require_api_key
from ..config import get_settings
from ..db import get_session
from ..ids import new_api_key, new_invite_token
from ..models import (
    Agent,
    AgentFolder,
    Alert,
    ApiKey,
    Call,
    Contact,
    QaCohort,
    WebhookDelivery,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    now_ms,
)
from ..schemas import CompatModel
from ..sessions import email_from_authorization
from .auth_google import _email_allowed

router = APIRouter(tags=["dashboard"])

DAY_MS = 86_400_000


# ------------------------------------------------------------------ analytics


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _series(counts: dict[str, float], start_ms: int, days: int) -> list[dict[str, Any]]:
    """Dense day series over the window (charts need every day present)."""
    return [
        {"date": day, "value": counts.get(day, 0)}
        for day in (_day(start_ms + i * DAY_MS) for i in range(days))
    ]


def _breakdown(counter: Counter) -> list[dict[str, Any]]:
    return [{"name": name, "value": value} for name, value in counter.most_common()]


@router.get("/analytics/calls")
async def call_analytics(
    days: int = 30,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    days = max(1, min(days, 365))
    # Window is the last `days` calendar days *including today*, so the series
    # ends on today's bucket.
    start_ms = now_ms() - (days - 1) * DAY_MS
    rows = (
        await session.scalars(
            select(Call).where(
                Call.workspace_id == api_key.workspace_id,
                Call.created_at_ms >= start_ms,
            )
        )
    ).all()

    durations = [c.duration_ms for c in rows if c.duration_ms]
    latencies: list[float] = []
    day_counts: Counter = Counter()
    day_minutes: Counter = Counter()
    successful: Counter = Counter()
    reasons: Counter = Counter()
    sentiments: Counter = Counter()
    directions: Counter = Counter()

    for c in rows:
        day_counts[_day(c.start_timestamp or c.created_at_ms)] += 1
        if c.duration_ms:
            day_minutes[_day(c.start_timestamp or c.created_at_ms)] += c.duration_ms / 60_000
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

    return {
        "call_counts": len(rows),
        "avg_duration_s": round(sum(durations) / len(durations) / 1000, 1) if durations else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "call_counts_series": _series(dict(day_counts), start_ms, days),
        "concurrency_series": _series(
            {d: round(m, 1) for d, m in day_minutes.items()}, start_ms, days
        ),
        "call_successful": _breakdown(successful),
        "disconnection_reason": _breakdown(reasons),
        "user_sentiment": _breakdown(sentiments),
        "phone_direction": _breakdown(directions),
    }


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


async def _get_folder(session: AsyncSession, folder_id: str, workspace_id: str) -> AgentFolder:
    folder = await session.get(AgentFolder, folder_id)
    if folder is None or folder.workspace_id != workspace_id:
        raise HTTPException(404, detail="Folder not found")
    return folder


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
    folder = await _get_folder(session, folder_id, api_key.workspace_id)
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
    folder = await _get_folder(session, folder_id, api_key.workspace_id)
    # Agents fall back to "no folder" — deleting a folder never deletes agents.
    await session.execute(
        update(Agent)
        .where(Agent.workspace_id == api_key.workspace_id, Agent.folder_id == folder_id)
        .values(folder_id=None)
    )
    await session.delete(folder)
    await session.commit()


# ------------------------------------------------------------------- contacts


def _contact_to_dict(c: Contact, related: int = 0, latest: int | None = None) -> dict[str, Any]:
    return {
        "contact_id": c.contact_id,
        "phone_number": c.phone_number,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "do_not_call": c.do_not_call,
        "external_id": c.external_id,
        "related_conversations": related,
        "latest_conversation": latest,
    }


class CreateContactRequest(CompatModel):
    phone_number: str
    first_name: str = ""
    last_name: str = ""
    do_not_call: bool = False
    external_id: str | None = None


@router.get("/list-contacts")
async def list_contacts(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    contacts = (
        await session.scalars(select(Contact).where(Contact.workspace_id == api_key.workspace_id))
    ).all()
    # Conversation stats come from the calls table, matched on either leg.
    stats: dict[str, tuple[int, int]] = {}
    for number_col in (Call.to_number, Call.from_number):
        result = await session.execute(
            select(number_col, func.count(), func.max(Call.created_at_ms))
            .where(Call.workspace_id == api_key.workspace_id, number_col.is_not(None))
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
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Contact not found")
    payload = await request.json()
    for field in ("phone_number", "first_name", "last_name", "do_not_call", "external_id"):
        if field in payload:
            setattr(contact, field, payload[field])
    await session.commit()
    return _contact_to_dict(contact)


@router.delete("/delete-contact/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Contact not found")
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
        "notify_emails": a.notify_emails or [],
        "webhook_url": a.webhook_url,
        "enabled": a.enabled,
    }


class CreateAlertRequest(CompatModel):
    name: str
    metric: str
    condition: str = "above"
    threshold: float = 0.0
    check_every_min: int = 5
    lookback_min: int = 60
    notify_emails: list[str] = []
    webhook_url: str | None = None
    enabled: bool = True


_ALERT_FIELDS = (
    "name",
    "metric",
    "condition",
    "threshold",
    "check_every_min",
    "lookback_min",
    "notify_emails",
    "webhook_url",
    "enabled",
)


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
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Alert not found")
    payload = await request.json()
    for field in _ALERT_FIELDS:
        if field in payload:
            setattr(alert, field, payload[field])
    await session.commit()
    return _alert_to_dict(alert)


@router.delete("/delete-alert/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Alert not found")
    await session.delete(alert)
    await session.commit()


# ----------------------------------------------------------------- QA cohorts


def _cohort_to_dict(c: QaCohort) -> dict[str, Any]:
    return {
        "cohort_id": c.cohort_id,
        "name": c.name,
        "agents": c.agents or [],
        "sampling_pct": c.sampling_pct,
        "weekly_max": c.weekly_max,
        # Scoring pipeline not built yet; the dashboard renders these as-is.
        "transfer_success_rate": 0,
        "transfer_wait_time_s": 0,
    }


class CreateCohortRequest(CompatModel):
    name: str
    agents: list[str] = []
    sampling_pct: float = 10.0
    weekly_max: int = 100


@router.get("/list-qa-cohorts")
async def list_qa_cohorts(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(select(QaCohort).where(QaCohort.workspace_id == api_key.workspace_id))
    ).all()
    return [_cohort_to_dict(c) for c in rows]


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
    cohort = await session.get(QaCohort, cohort_id)
    if cohort is None or cohort.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Cohort not found")
    await session.delete(cohort)
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
    api_key: ApiKey = Depends(require_api_key),
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
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ApiKey, key_id)
    if row is None or row.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="API key not found")
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


@router.get("/workspace")
async def get_workspace(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    ws = await session.get(Workspace, api_key.workspace_id)
    if ws is None:
        raise HTTPException(404, detail="Workspace not found")
    return {"workspace_id": ws.id, "name": ws.name, "webhook_url": ws.webhook_url}


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
    for field in ("name", "webhook_url"):
        if field in payload:
            setattr(ws, field, payload[field])
    await session.commit()
    return {"workspace_id": ws.id, "name": ws.name, "webhook_url": ws.webhook_url}


# --------------------------------------------------------- members & invites

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


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

    A raw API key carries no personal identity and is operator credentials,
    so it manages members unrestricted (also the dev-mode path). A session
    JWT must belong to an owner or admin of the workspace — or an allowlisted
    email, which is owner-by-definition: its member row is only written at
    login, so sessions issued before that row exists must not be locked out.
    """
    email = email_from_authorization(authorization)
    if email is None:
        return MemberManager(api_key, None)
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == api_key.workspace_id,
            WorkspaceMember.email == email,
        )
    )
    if (member is None or member.role not in ("owner", "admin")) and not _email_allowed(email):
        raise HTTPException(403, detail="Only workspace owners and admins can manage members")
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
    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL_RE)
    role: str = Field(default="member", pattern="^(admin|member)$")

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: Any) -> Any:
        # Members and invites store emails lowercase; normalize before the
        # pattern check so validation sees the stored form.
        return v.strip().lower() if isinstance(v, str) else v


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
    invite = await session.get(WorkspaceInvite, invite_id)
    if invite is None or invite.workspace_id != manager.api_key.workspace_id:
        raise HTTPException(404, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(409, detail=f"Invite is already {invite.status}")
    invite.status = "revoked"
    await session.commit()


class RemoveMemberRequest(CompatModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


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
