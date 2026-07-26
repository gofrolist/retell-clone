# Agent versioning

Voice agents keep a version history. Published versions are immutable snapshots
and one of them is what live calls run; editing an agent opens a **draft** that
traffic never sees until it is published.

This exists so a prompt edit can't change an agent that is mid-shift. Before it,
every write path read the live `agents` / `retell_llms` rows, so saving in the
dashboard took effect on the next call — and on any config refetch during a call
already in progress.

Chat agents and conversation flows are **not** versioned; they keep their plain
`version` counter.

## Model

`agent_versions` (composite PK `(agent_id, version)`), plus
`agents.published_version`.

- Versions number `0..N` monotonically. `agents.published_version` names the one
  live calls resolve against.
- **Published versions are immutable.** Their config is frozen in
  `agent_snapshot` + `llm_snapshot` (raw column values for everything except
  identity/bookkeeping columns, so a column added later is captured without a
  second allowlist to maintain).
- **At most one draft**, and it is always the highest version `N`. A draft stores
  no snapshot — *its content is the live `agents` + `retell_llms` rows*. That is
  what keeps autosave cheap: editing goes through the ordinary
  `update-agent` / `update-retell-llm` paths and costs no snapshot.
- With no draft open, `N == published_version` and the live rows mirror it, so
  the next config edit forks `N+1` first.
- `agents.is_published` means "the latest version is published", i.e. no draft is
  open.

Agents that predate the feature are seeded lazily (`services/versions.ensure_seeded`)
on first touch rather than by a boot-time data migration: an image rollback stays
harmless, and read paths fall back to the live rows when no history exists yet.

`services/versions.py` owns every one of these rules. Routers call it; they don't
reimplement it.

## Lifecycle

| Action | Effect |
|---|---|
| `POST /create-agent` | `V0`, published. New agents take calls immediately. |
| `PATCH /update-agent` / `PATCH /update-retell-llm` | Forks a draft from the latest version if none is open, then edits the live rows. `version` in the response names the draft. A folder-only move changes nothing. |
| `POST /publish-agent-version/{id}` `{version}` | A draft is snapshotted and becomes published; an already-published version is simply re-pointed at (rollback, no new version). |
| `POST /publish-agent/{id}` | Publishes the open draft. Optional `version_title` / `version_description`. |
| `POST /create-agent-version/{id}` `{base_version}` | Opens a draft carrying that version's config — how a restore works. 409 if a draft is already open. |
| `DELETE /delete-agent-version/{id}/{version}` | Discards a draft and puts the live rows back where it branched from. 422 on a published version. |

## Resolution

`versions.resolve(session, agent, ref)` where `ref` is a version number,
`"latest"`, or `"latest_published"`.

- **Calls default to `latest_published`.** `create-phone-call`,
  `register-phone-call`, `create-web-call`, `create-batch-call` and inbound
  resolution all stamp `Call.agent_version` from it.
- That stamp then **pins the call**: `GET /internal/calls/{id}/config` serves the
  pinned version, so publishing mid-call can't swap config under a running
  session (the worker refetches config on reconnects).
- A pinned version whose row is *gone* — a call stamped by the pre-versioning
  counter, or one pinned to a since-discarded draft — degrades to the published
  version rather than 404ing (`resolve(..., strict=False)`). A raise there would
  kill a live call. An explicitly requested unknown version still 404s.
- `override_agent_version` (phone calls) and `agent_version` (web/register calls)
  override the default. The editor's Test Audio button passes the version being
  edited (`TestPanel` → `api.createWebCall`), so a draft can be voice-tested
  before it is published.
- `agent_swap` (`GET /internal/agents/{id}/config`) lands on the destination
  agent's published version.
- `GET /get-agent/{id}` still defaults to the **latest** version — what the
  editor shows. Pass `?version=latest_published` for what calls run.
- Simulation and the inline test chat deliberately use the live rows: you test
  what you are editing.

## Dashboard

`components/editor/VersionsPanel.tsx` is the third editor column. Drafts and
Published are separate groups; each row shows its title/description, `From Vxx`
lineage, timestamp, and a **Live** badge on the published version calls resolve
to. Selecting an older version loads it read-only (`GET /get-agent-version/{id}/{version}`,
which carries the prompt); **Restore** branches a draft from it.

There is no Save button. Edits debounce into the draft (`AUTOSAVE_MS` in
`app/agents/[id]/page.tsx`), the header shows `Saving…` / `Saved`, and Publish is
the only commit.

## Differences from Retell

- **One draft at a time.** Retell allows several simultaneously; we return 409
  when branching with a draft already open. One editable version removes the
  "which draft am I editing" ambiguity in the dashboard.
- **No environment tags** (`prod` / `staging`) yet, so `agent_version` accepts a
  number, `latest` or `latest_published` — not a tag name.
- `is_live` on a version entry is an Arhiteq extra: publishing an older version
  re-points production without minting a new version, so "published" and "live"
  are not the same thing.
- **Calls with no `agent_version` resolve the published version, not the latest.**
  This is the point of the feature, but it is a behaviour change for any
  integration that PATCHes an agent and expects the next call to pick the edit
  up — such a caller now has to publish. (Arhiteq's own consumer only creates
  calls; it never updates agents. See `docs/RETELL_INTEGRATION_MAP.md`.)

## Sharing one LLM between agents

`response_engine.llm_id` can point several agents at one `retell_llms` row. That
row is a single config for all of them — editing the prompt for one agent has
always changed the others, before versioning and after.

Restore and discard-draft would make that worse, because they write a whole
snapshot back. So `_restore_config` forks a **private copy** of the LLM for the
restoring agent (repointing its `response_engine`) whenever the write would
actually change a row another agent uses. A restore that wouldn't change the
config writes nothing and forks nothing.

Ordinary prompt edits are untouched by this and still propagate to every agent
on that LLM. The dashboard gives each agent its own LLM (including on
"Duplicate agent"), so sharing only arises through direct API use.
