"""Agent version history: drafts, immutable published snapshots, resolution.

The invariants (also written up in docs/AGENT_VERSIONING.md):

- Versions number 0..N monotonically in `agent_versions`. `Agent.published_version`
  names the one live calls resolve against; published versions never change.
- At most one draft, always the highest version N, and its content *is* the live
  `agents` + `retell_llms` rows. Snapshots are taken once, at publish — so the
  editor keeps writing through the ordinary update-agent / update-retell-llm
  paths and no keystroke costs a snapshot.
- Agents that predate versioning are seeded lazily on first touch rather than by
  a boot-time data migration, so a rollback to an older image stays harmless.

Only voice agents are versioned. Chat agents and conversation flows keep their
plain `version` counter.
"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentVersion, ConversationFlow, RetellLLM, now_ms

LATEST = "latest"
LATEST_PUBLISHED = "latest_published"

# Columns that identify a row or track version bookkeeping rather than describe
# the agent's behaviour. Everything else is config and travels in a snapshot,
# so a column added later is captured without touching a second allowlist.
# folder_id is excluded deliberately: a folder move is dashboard-only regrouping
# and must not differ between versions (see api/agents.py update_agent).
_AGENT_EXCLUDED = frozenset(
    {
        "agent_id",
        "workspace_id",
        "version",
        "is_published",
        "published_version",
        "last_modification_timestamp",
        "folder_id",
    }
)
_LLM_EXCLUDED = frozenset({"llm_id", "workspace_id", "version", "last_modification_timestamp"})
_FLOW_EXCLUDED = frozenset(
    {
        "conversation_flow_id",
        "workspace_id",
        "version",
        "last_modification_timestamp",
        "created_at_ms",
    }
)


def _snapshot(obj: Any, excluded: frozenset[str]) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name not in excluded}


def _detach(live: Any, snapshot: dict[str, Any] | None, excluded: frozenset[str]) -> Any:
    """Live row overlaid with `snapshot`, as a transient (session-less) object.

    Returning a real ORM instance keeps agent_to_dict/llm_to_dict the only
    serializers. Columns added after the snapshot was written aren't in it and
    keep the live row's value — the snapshot degrades gracefully instead of
    serving NULL for a field the wire contract promises.
    """
    clone = type(live)()
    for column in live.__table__.columns:
        setattr(clone, column.name, getattr(live, column.name))
    for field, value in (snapshot or {}).items():
        if field in live.__table__.columns and field not in excluded:
            setattr(clone, field, value)
    return clone


def _restore(live: Any, snapshot: dict[str, Any] | None, excluded: frozenset[str]) -> None:
    """Write `snapshot` back onto the live row (restore / discard-draft)."""
    for field, value in (snapshot or {}).items():
        if field in live.__table__.columns and field not in excluded:
            setattr(live, field, value)
    live.last_modification_timestamp = now_ms()


async def _load_llm(session: AsyncSession, agent: Agent) -> RetellLLM | None:
    llm_id = (agent.response_engine or {}).get("llm_id")
    if not llm_id:
        return None
    return await session.get(RetellLLM, llm_id)


async def _load_flow(session: AsyncSession, agent: Agent) -> ConversationFlow | None:
    flow_id = (agent.response_engine or {}).get("conversation_flow_id")
    if not flow_id:
        return None
    return await session.get(ConversationFlow, flow_id)


async def history(session: AsyncSession, agent_id: str) -> list[AgentVersion]:
    """All versions of an agent, newest first."""
    rows = await session.scalars(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
    )
    return list(rows)


async def _get(session: AsyncSession, agent_id: str, version: int) -> AgentVersion | None:
    return await session.get(AgentVersion, (agent_id, version))


async def get_row(session: AsyncSession, agent_id: str, version: int) -> AgentVersion | None:
    return await _get(session, agent_id, version)


async def current_row(session: AsyncSession, agent: Agent) -> AgentVersion | None:
    """The version the live rows mirror — the open draft, or the latest."""
    return await _get(session, agent.agent_id, agent.version)


async def ensure_seeded(session: AsyncSession, agent: Agent) -> None:
    """Give an agent that predates versioning its initial published version.

    Idempotent, and safe to call from read paths: agents created after this
    ships already have V0 from `record_initial`.
    """
    existing = await session.scalar(
        select(AgentVersion.version).where(AgentVersion.agent_id == agent.agent_id).limit(1)
    )
    if existing is not None:
        if agent.published_version is None:
            agent.published_version = agent.version
        return
    llm = await _load_llm(session, agent)
    flow = await _load_flow(session, agent)
    session.add(
        AgentVersion(
            agent_id=agent.agent_id,
            version=agent.version,
            workspace_id=agent.workspace_id,
            base_version=None,
            is_published=True,
            version_description="Imported from live configuration",
            agent_snapshot=_snapshot(agent, _AGENT_EXCLUDED),
            llm_snapshot=_snapshot(llm, _LLM_EXCLUDED) if llm is not None else None,
            flow_snapshot=_snapshot(flow, _FLOW_EXCLUDED) if flow is not None else None,
            created_timestamp=agent.last_modification_timestamp,
            last_modification_timestamp=agent.last_modification_timestamp,
            published_timestamp=agent.last_modification_timestamp,
        )
    )
    agent.published_version = agent.version
    agent.is_published = True
    await session.flush()


async def record_initial(session: AsyncSession, agent: Agent) -> AgentVersion:
    """Create V0, published, for a freshly created agent.

    New agents start published so imported consumer agents take calls
    immediately — matching the pre-versioning `is_published=True` default.
    """
    llm = await _load_llm(session, agent)
    flow = await _load_flow(session, agent)
    version = AgentVersion(
        agent_id=agent.agent_id,
        version=agent.version,
        workspace_id=agent.workspace_id,
        base_version=None,
        is_published=True,
        agent_snapshot=_snapshot(agent, _AGENT_EXCLUDED),
        llm_snapshot=_snapshot(llm, _LLM_EXCLUDED) if llm is not None else None,
        flow_snapshot=_snapshot(flow, _FLOW_EXCLUDED) if flow is not None else None,
        published_timestamp=agent.last_modification_timestamp,
    )
    session.add(version)
    agent.published_version = agent.version
    agent.is_published = True
    return version


async def touch(session: AsyncSession, agent: Agent) -> AgentVersion:
    """Route a config edit into a draft, forking one from the latest if needed.

    Called *after* the patch has been applied to the live rows: the draft's
    content is those rows, so there is nothing to copy.
    """
    await ensure_seeded(session, agent)
    if not agent.is_published:
        draft = await _get(session, agent.agent_id, agent.version)
        if draft is not None:
            draft.last_modification_timestamp = now_ms()
            return draft
        # Defensive: is_published said "draft open" but no row backs it.
        agent.is_published = True
    base = agent.version
    agent.version = base + 1
    agent.is_published = False
    draft = AgentVersion(
        agent_id=agent.agent_id,
        version=agent.version,
        workspace_id=agent.workspace_id,
        base_version=base,
        is_published=False,
    )
    session.add(draft)
    await session.flush()
    return draft


async def agents_using_llm(session: AsyncSession, workspace_id: str, llm_id: str) -> list[Agent]:
    """Agents whose response engine is this LLM.

    Filtered in Python rather than with a JSON predicate, which would need
    dialect-specific SQL for SQLite and Postgres both; a workspace holds tens
    of agents, not thousands.
    """
    agents = await session.scalars(select(Agent).where(Agent.workspace_id == workspace_id))
    return [a for a in agents if (a.response_engine or {}).get("llm_id") == llm_id]


async def publish(
    session: AsyncSession,
    agent: Agent,
    version: int,
    *,
    title: str | None = None,
    description: str | None = None,
) -> AgentVersion:
    """Make `version` the one live calls resolve against.

    A draft is frozen into a snapshot first. An already-published version is
    simply re-pointed at — that is a rollback, and it mints no new version.
    """
    await ensure_seeded(session, agent)
    row = await _get(session, agent.agent_id, version)
    if row is None:
        raise HTTPException(404, detail=f"Agent version {version} not found")
    if not row.is_published:
        llm = await _load_llm(session, agent)
        flow = await _load_flow(session, agent)
        row.agent_snapshot = _snapshot(agent, _AGENT_EXCLUDED)
        row.llm_snapshot = _snapshot(llm, _LLM_EXCLUDED) if llm is not None else None
        row.flow_snapshot = _snapshot(flow, _FLOW_EXCLUDED) if flow is not None else None
        row.is_published = True
        row.published_timestamp = now_ms()
        agent.is_published = True
        # Labels are part of a published version and freeze with it. On the
        # rollback path (`row` already published) they are ignored rather than
        # rewriting an existing entry's changelog.
        if title is not None:
            row.version_title = title
        if description is not None:
            row.version_description = description
        row.last_modification_timestamp = now_ms()
    agent.published_version = version
    agent.last_modification_timestamp = now_ms()
    return row


async def branch(session: AsyncSession, agent: Agent, base_version: int) -> AgentVersion:
    """Open a draft carrying `base_version`'s config — Retell-style rollback.

    Restoring an old version means branching a draft from it and publishing
    that, so the history stays append-only.
    """
    await ensure_seeded(session, agent)
    if not agent.is_published:
        raise HTTPException(
            409,
            detail=(
                f"A draft (V{agent.version}) is already open. Publish or discard it "
                "before branching from another version."
            ),
        )
    base = await _get(session, agent.agent_id, base_version)
    if base is None:
        raise HTTPException(404, detail=f"Agent version {base_version} not found")
    await _restore_config(session, agent, base)
    agent.version = (await _max_version(session, agent.agent_id)) + 1
    agent.is_published = False
    draft = AgentVersion(
        agent_id=agent.agent_id,
        version=agent.version,
        workspace_id=agent.workspace_id,
        base_version=base_version,
        is_published=False,
    )
    session.add(draft)
    await session.flush()
    return draft


async def discard(session: AsyncSession, agent: Agent, version: int) -> None:
    """Drop the open draft; the live rows fall back to the newest version left.

    Not the draft's `base_version`: the live rows must mirror the highest
    remaining version to keep the invariant, and a draft branched from an old
    version leaves a newer published one behind.
    """
    row = await _get(session, agent.agent_id, version)
    if row is None:
        raise HTTPException(404, detail=f"Agent version {version} not found")
    if row.is_published:
        raise HTTPException(422, detail="Published versions are immutable and cannot be deleted")
    await session.delete(row)
    await session.flush()
    agent.version = await _max_version(session, agent.agent_id)
    agent.is_published = True
    remaining = await _get(session, agent.agent_id, agent.version)
    if remaining is not None:
        await _restore_config(session, agent, remaining)


async def _restore_config(session: AsyncSession, agent: Agent, version: AgentVersion) -> None:
    """Write a version's snapshot back onto the live agent + LLM rows.

    Order matters: the agent snapshot carries `response_engine`, so it has to
    land *before* the LLM is loaded. Loading first would fetch whatever engine
    the outgoing draft pointed at and then write the restored version's prompt
    into that unrelated LLM.
    """
    _restore(agent, version.agent_snapshot, _AGENT_EXCLUDED)
    llm = await _load_llm(session, agent)
    if llm is None:
        return
    snapshot = version.llm_snapshot or {}
    if all(getattr(llm, field, None) == value for field, value in snapshot.items()):
        return  # already the restored config — nothing to write, nothing to fork
    # An LLM shared by several agents is one config for all of them (that is
    # already true of ordinary prompt edits), so restoring would rewrite the
    # other agents' prompt. Fork a private copy instead of corrupting theirs.
    sharers = [
        a
        for a in await agents_using_llm(session, agent.workspace_id, llm.llm_id)
        if a.agent_id != agent.agent_id
    ]
    if sharers:
        copy = RetellLLM(
            workspace_id=agent.workspace_id,
            **{
                c.name: getattr(llm, c.name)
                for c in llm.__table__.columns
                if c.name not in _LLM_EXCLUDED
            },
        )
        session.add(copy)
        await session.flush()
        agent.response_engine = {**(agent.response_engine or {}), "llm_id": copy.llm_id}
        llm = copy
    _restore(llm, version.llm_snapshot, _LLM_EXCLUDED)


async def _max_version(session: AsyncSession, agent_id: str) -> int:
    highest = await session.scalar(
        select(AgentVersion.version)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
        .limit(1)
    )
    return highest if highest is not None else 0


def parse_ref(raw: Any) -> int | str | None:
    """Normalize a wire `version` reference into an int or a LATEST* keyword."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise HTTPException(
            422, detail="version must be an integer, 'latest' or 'latest_published'"
        )
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if text in (LATEST, LATEST_PUBLISHED):
        return text
    try:
        return int(text)
    except ValueError:
        raise HTTPException(
            422, detail="version must be an integer, 'latest' or 'latest_published'"
        ) from None


async def resolve(
    session: AsyncSession,
    agent: Agent,
    ref: int | str | None = LATEST_PUBLISHED,
    *,
    strict: bool = True,
) -> tuple[Agent, RetellLLM | None, int]:
    """Config an agent runs under `ref`, as (agent, llm, version_number).

    Returns the live rows when `ref` names the latest version (a draft has no
    snapshot — it *is* the live rows) or when the agent has no history yet, so
    every caller works before the lazy seed has run.

    `strict` controls what an unknown version number does. Callers passing a
    user-supplied version want the 404; callers resolving a version *pinned on
    an existing call* must not, because the row can legitimately be gone (a
    call stamped by the pre-versioning counter, or a draft since discarded) and
    a raise there would kill a live call.
    """
    llm = await _load_llm(session, agent)
    if ref == LATEST:
        return agent, llm, agent.version
    target = agent.published_version if ref is None or ref == LATEST_PUBLISHED else int(ref)
    if target is None or target == agent.version:
        return agent, llm, agent.version
    row = await _get(session, agent.agent_id, target)
    if row is None:
        if isinstance(ref, int):
            if strict:
                raise HTTPException(404, detail=f"Agent version {target} not found")
            if target != agent.published_version:
                # Degrade to what the agent is serving now, not to the live
                # rows, which may hold an unpublished draft.
                return await resolve(session, agent, LATEST_PUBLISHED, strict=False)
        return agent, llm, agent.version
    if row.agent_snapshot is None:  # an out-of-band draft; nothing pinned to serve
        return agent, llm, agent.version
    pinned = _detach(agent, row.agent_snapshot, _AGENT_EXCLUDED)
    pinned.version = row.version
    pinned.is_published = row.is_published
    pinned_llm = _detach(llm, row.llm_snapshot, _LLM_EXCLUDED) if llm is not None else None
    return pinned, pinned_llm, row.version


async def resolve_with_flow(
    session: AsyncSession,
    agent: Agent,
    ref: int | str | None = LATEST_PUBLISHED,
    *,
    strict: bool = True,
) -> tuple[Agent, RetellLLM | None, ConversationFlow | None, int]:
    """`resolve()` plus the conversation flow pinned at that version.

    Split from resolve() rather than widening its tuple: only the internal
    config endpoints need the flow, and the other eight call sites would all
    have to grow an ignored slot.

    Deliberately does NOT shortcut on `version == agent.version` the way the
    agent/llm snapshot lookups implicitly do inside resolve(): that equality
    only proves the *agent* row wasn't drafted since publish. A conversation
    flow keeps its own independent version counter (see module docstring) and
    `update-conversation-flow` never touches the owning agent's draft state,
    so a flow can drift out from under a published, untouched agent. The
    version's own row is always consulted; only a version with no row (a
    fresh, still-open draft) or no flow_snapshot (pre-Task-5 history, or an
    agent with no flow) falls back to serving the live flow.
    """
    pinned, llm, version = await resolve(session, agent, ref, strict=strict)
    flow = await _load_flow(session, pinned)
    if flow is None:
        return pinned, llm, flow, version
    row = await _get(session, agent.agent_id, version)
    if row is None or row.flow_snapshot is None:
        return pinned, llm, flow, version
    return pinned, llm, _detach(flow, row.flow_snapshot, _FLOW_EXCLUDED), version


async def published_version_of(session: AsyncSession, agent: Agent) -> int:
    """The version number a new call should be stamped with."""
    return agent.published_version if agent.published_version is not None else agent.version


def to_dict(
    row: AgentVersion, config: dict[str, Any], *, live_version: int | None
) -> dict[str, Any]:
    """A version-history entry: the agent shape at that version plus lineage."""
    return {
        **config,
        "version": row.version,
        "is_published": row.is_published,
        "base_version": row.base_version,
        "version_title": row.version_title,
        "version_description": row.version_description,
        # Arhiteq extra: which entry live calls actually resolve to. Publishing
        # an older version re-points this without minting a new version.
        "is_live": row.version == live_version,
        "created_timestamp": row.created_timestamp,
        "published_timestamp": row.published_timestamp,
        "last_modification_timestamp": row.last_modification_timestamp,
    }
