# Conversation Flow Storage & Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store Retell conversation flows without losing a single field, and make a
published agent version freeze its flow the way it already freezes its LLM.

**Architecture:** Widen the `conversation_flows` table and its request/response
schemas to cover every field the live Retell API returns, proven by round-tripping
three real flows committed as fixtures. Then teach `services/versions.py` a third
snapshot object (`flow_snapshot` on `agent_versions`) alongside the agent and LLM
ones, and expose the resolved flow to the worker over the internal API.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 (async), Pydantic v2, pytest
(`asyncio_mode=auto`), uv.

This is **plan 1 of 3** from
`docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md`. It ships
a standalone bug fix (silent field drops on flow import) and the versioning
foundation. Plan 2 is the worker runtime; plan 3 is the editor. Nothing here
turns on any user-visible feature — the create-modal card stays disabled.

## Global Constraints

- **Never rename or drop a wire field.** Extra fields are fine; renames and drops
  are not. (`CLAUDE.md`, prime directive.)
- Python 3.14, uv, `uv_build` backend. Backend package is `arhiteq_api` under
  `backend/src/`.
- Backend tests: `cd backend && uv run pytest`.
- No Alembic. Schema changes are additive `create_all` plus idempotent entries in
  `_COLUMN_BACKFILLS` (`backend/src/arhiteq_api/main.py:55`).
- Tests must pass on SQLite (test DB) and Postgres (prod). No dialect-specific SQL.
- `main` is protected. Work on branch `feat/conversation-flow-agents`; PR title must
  be a conventional commit.
- pre-commit runs gitleaks, ruff check + format, pytest, eslint on commit.
- The three fixtures in `backend/tests/fixtures/retell_flows/` are sanitized real
  Retell data. **Never edit them by hand** — they are the schema authority.

## Where node validation lives

The spec is explicit about this (§ "Where node validation lives"), and it is
worth restating because it shapes what this plan does *not* build. `backend/` and
`worker/` are separate uv projects with no shared package, so there is no one
module to hold node validation. The arrangement:

- **The API validates nothing about node types.** It stores whatever it is given,
  so importing a flow containing an `mcp` or `component` node is lossless.
- **The worker** enforces the supported-type list at call start (plan 2).
- The fixtures, read by both test suites, are what keep the two honest.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/src/arhiteq_api/models.py` | `ConversationFlow` columns; `AgentVersion.flow_snapshot` | modify |
| `backend/src/arhiteq_api/main.py` | `_COLUMN_BACKFILLS` entries for the new columns | modify |
| `backend/src/arhiteq_api/schemas_extra.py` | `CreateConversationFlowRequest`, `conversation_flow_to_dict` | modify |
| `backend/src/arhiteq_api/api/conversation_flows.py` | `_MUTABLE_FIELDS` | modify |
| `backend/src/arhiteq_api/api/agents.py` | validate `conversation_flow_id` on create/update | modify |
| `backend/src/arhiteq_api/services/versions.py` | `_load_flow`, `_FLOW_EXCLUDED`, snapshot/restore/resolve | modify |
| `backend/src/arhiteq_api/api/internal.py` | return `conversation_flow` in call/agent config | modify |
| `backend/tests/contract/test_conversation_flow_fidelity.py` | fixture round-trip guard | create |
| `backend/tests/contract/test_flow_agents.py` | flow-backed agent create + version snapshot | create |
| `docs/API_COVERAGE.md`, `docs/AGENT_VERSIONING.md` | documented behaviour | modify |

---

### Task 1: Fixture round-trip test (red)

Proves the field-drop bug exists before fixing it. This test must FAIL at the end
of this task — that is the deliverable.

**Files:**
- Create: `backend/tests/contract/test_conversation_flow_fidelity.py`

**Interfaces:**
- Consumes: `tests.conftest.AUTH_HEADERS`, the `client` fixture
  (`backend/tests/conftest.py:51`, `:141`), and the three JSON files in
  `backend/tests/fixtures/retell_flows/`.
- Produces: `FIXTURE_DIR`, `load_fixture(name)` and `SERVER_MANAGED` — reused by
  Task 6's test module.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/contract/test_conversation_flow_fidelity.py`:

```python
"""Real Retell flows must survive a create/read round-trip byte-for-byte.

The fixtures are sanitized captures from a live Retell account (see
docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md). They are
the schema authority: if the API drops a field they carry, that is a contract
break, because the migration script copies flows verbatim.
"""

import json
from pathlib import Path

import pytest

from tests.conftest import AUTH_HEADERS

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "retell_flows"

# The API mints or bumps these, so a fixture's values can never match.
SERVER_MANAGED = frozenset(
    {"conversation_flow_id", "version", "last_modification_timestamp"}
)

FIXTURE_NAMES = sorted(p.name for p in FIXTURE_DIR.glob("*.json"))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def test_fixtures_are_present():
    """Guard against an empty glob silently parametrizing zero tests."""
    assert FIXTURE_NAMES, f"no fixtures found in {FIXTURE_DIR}"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
async def test_retell_flow_round_trips_unchanged(client, name):
    source = load_fixture(name)
    payload = {k: v for k, v in source.items() if k not in SERVER_MANAGED}

    created = await client.post(
        "/create-conversation-flow", headers=AUTH_HEADERS, json=payload
    )
    assert created.status_code == 201, created.text

    got = await client.get(
        f"/get-conversation-flow/{created.json()['conversation_flow_id']}",
        headers=AUTH_HEADERS,
    )
    assert got.status_code == 200, got.text
    body = got.json()

    dropped = sorted(k for k in payload if k not in body)
    assert not dropped, f"{name}: fields dropped by the API: {dropped}"

    altered = sorted(k for k, v in payload.items() if body[k] != v)
    assert not altered, f"{name}: fields altered by the API: {altered}"
```

- [ ] **Step 2: Run the test and confirm it fails for the right reason**

```bash
cd backend && uv run pytest tests/contract/test_conversation_flow_fidelity.py -v
```

Expected: `test_fixtures_are_present` PASSES; all three
`test_retell_flow_round_trips_unchanged` cases FAIL with
`fields dropped by the API: ['begin_tag_display_position', 'components', ...]`.

If instead they fail with a 422 or 500, stop — that is a different bug and the
plan's assumptions need re-checking.

- [ ] **Step 3: Commit the red test**

```bash
git add backend/tests/contract/test_conversation_flow_fidelity.py
SKIP=pytest-backend git commit -m "test(flows): failing round-trip guard for real Retell flows"
```

`SKIP=pytest-backend` is required here and only here — the pre-commit pytest hook would
block a deliberately-red test. Skip only that hook, not all of them: gitleaks,
ruff check and ruff format still run. Do not use `--no-verify`. Every later
commit runs the hooks normally.

---

### Task 2: Widen conversation flow storage (green)

**Files:**
- Modify: `backend/src/arhiteq_api/models.py:497-513` (`ConversationFlow`)
- Modify: `backend/src/arhiteq_api/main.py:55-71` (`_COLUMN_BACKFILLS`)
- Modify: `backend/src/arhiteq_api/schemas_extra.py:60-67`
  (`CreateConversationFlowRequest`)
- Modify: `backend/src/arhiteq_api/schemas_extra.py:163-175`
  (`conversation_flow_to_dict`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ConversationFlow` gains the columns `components`, `notes`,
  `kb_config`, `knowledge_base_ids`, `mcps`, `begin_tag_display_position`,
  `tool_call_strict_mode`, `is_transfer_cf`, `is_transfer_llm`, `flex_mode`,
  `is_published`, `model_temperature`. Tasks 4–6 read these names.

- [ ] **Step 1: Add the columns to the model**

In `backend/src/arhiteq_api/models.py`, inside `class ConversationFlow`, after the
existing `default_dynamic_variables` line and before
`last_modification_timestamp`:

```python
    # Fields the live Retell API returns that predate this table's first cut.
    # All nullable: a flow authored in our dashboard sets few of them.
    components: Mapped[list[Any] | None] = mapped_column(JSON)
    notes: Mapped[list[Any] | None] = mapped_column(JSON)
    kb_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    knowledge_base_ids: Mapped[list[Any] | None] = mapped_column(JSON)
    mcps: Mapped[list[Any] | None] = mapped_column(JSON)
    begin_tag_display_position: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_temperature: Mapped[float | None] = mapped_column(Float)
    tool_call_strict_mode: Mapped[bool | None] = mapped_column(Boolean)
    # Retell's docs name this is_transfer_llm, the live API returns
    # is_transfer_cf. Store both rather than guessing which one a caller sends.
    is_transfer_cf: Mapped[bool | None] = mapped_column(Boolean)
    is_transfer_llm: Mapped[bool | None] = mapped_column(Boolean)
    flex_mode: Mapped[bool | None] = mapped_column(Boolean)
    is_published: Mapped[bool | None] = mapped_column(Boolean)
```

`JSON`, `Boolean` and `Float` are already imported (`models.py:4-17`). No new
imports needed.

- [ ] **Step 2: Add the column backfills**

In `backend/src/arhiteq_api/main.py`, inside the `_COLUMN_BACKFILLS` tuple, after
the `("retell_llms", "mcps", "JSON"),` line:

```python
    ("conversation_flows", "components", "JSON"),
    ("conversation_flows", "notes", "JSON"),
    ("conversation_flows", "kb_config", "JSON"),
    ("conversation_flows", "knowledge_base_ids", "JSON"),
    ("conversation_flows", "mcps", "JSON"),
    ("conversation_flows", "begin_tag_display_position", "JSON"),
    ("conversation_flows", "model_temperature", "FLOAT"),
    ("conversation_flows", "tool_call_strict_mode", "BOOLEAN"),
    ("conversation_flows", "is_transfer_cf", "BOOLEAN"),
    ("conversation_flows", "is_transfer_llm", "BOOLEAN"),
    ("conversation_flows", "flex_mode", "BOOLEAN"),
    ("conversation_flows", "is_published", "BOOLEAN"),
```

- [ ] **Step 3: Widen the request schema**

Replace `CreateConversationFlowRequest` in
`backend/src/arhiteq_api/schemas_extra.py`:

```python
class CreateConversationFlowRequest(CompatModel):
    nodes: list[dict[str, Any]]
    start_speaker: str = "agent"
    model_choice: dict[str, Any] | None = None
    global_prompt: str | None = None
    start_node_id: str | None = None
    tools: list[dict[str, Any]] | None = None
    default_dynamic_variables: dict[str, Any] | None = None
    components: list[dict[str, Any]] | None = None
    notes: list[dict[str, Any]] | None = None
    kb_config: dict[str, Any] | None = None
    knowledge_base_ids: list[str] | None = None
    mcps: list[dict[str, Any]] | None = None
    begin_tag_display_position: dict[str, Any] | None = None
    model_temperature: float | None = None
    tool_call_strict_mode: bool | None = None
    is_transfer_cf: bool | None = None
    is_transfer_llm: bool | None = None
    flex_mode: bool | None = None
    is_published: bool | None = None
```

- [ ] **Step 4: Persist the new fields on create**

In `backend/src/arhiteq_api/api/conversation_flows.py`, replace the body of
`create_conversation_flow` between the `start_node_id` inference and
`session.add(flow)`:

```python
    flow = ConversationFlow(
        workspace_id=api_key.workspace_id,
        global_prompt=body.global_prompt,
        nodes=body.nodes,
        start_node_id=start_node_id,
        start_speaker=body.start_speaker,
        model_choice=body.model_choice,
        tools=body.tools,
        default_dynamic_variables=body.default_dynamic_variables,
        components=body.components,
        notes=body.notes,
        kb_config=body.kb_config,
        knowledge_base_ids=body.knowledge_base_ids,
        mcps=body.mcps,
        begin_tag_display_position=body.begin_tag_display_position,
        model_temperature=body.model_temperature,
        tool_call_strict_mode=body.tool_call_strict_mode,
        is_transfer_cf=body.is_transfer_cf,
        is_transfer_llm=body.is_transfer_llm,
        flex_mode=body.flex_mode,
        is_published=body.is_published,
    )
```

- [ ] **Step 5: Widen the serializer**

Replace `conversation_flow_to_dict` in `backend/src/arhiteq_api/schemas_extra.py`:

```python
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
        "components": cf.components,
        "notes": cf.notes,
        "kb_config": cf.kb_config,
        "knowledge_base_ids": cf.knowledge_base_ids,
        "mcps": cf.mcps,
        "begin_tag_display_position": cf.begin_tag_display_position,
        "model_temperature": cf.model_temperature,
        "tool_call_strict_mode": cf.tool_call_strict_mode,
        "is_transfer_cf": cf.is_transfer_cf,
        "is_transfer_llm": cf.is_transfer_llm,
        "flex_mode": cf.flex_mode,
        "is_published": cf.is_published,
        "last_modification_timestamp": cf.last_modification_timestamp,
    }
```

- [ ] **Step 6: Run the fidelity test — it must now pass**

```bash
cd backend && uv run pytest tests/contract/test_conversation_flow_fidelity.py -v
```

Expected: 4 passed (the presence guard + three fixtures).

- [ ] **Step 7: Run the whole conversation flow suite**

```bash
cd backend && uv run pytest tests/contract/test_conversation_flow.py -v
```

Expected: all pass. The pre-existing tests assert a subset of keys, so widening
the serializer cannot break them.

- [ ] **Step 8: Commit**

```bash
git add backend/src/arhiteq_api/models.py backend/src/arhiteq_api/main.py \
        backend/src/arhiteq_api/schemas_extra.py \
        backend/src/arhiteq_api/api/conversation_flows.py
git commit -m "fix(flows): stop dropping conversation flow fields on write"
```

---

### Task 3: Make the new fields updatable

Create is lossless after Task 2; `PATCH` still silently ignores the new fields.

**Files:**
- Modify: `backend/src/arhiteq_api/api/conversation_flows.py:13-21` (`_MUTABLE_FIELDS`)
- Modify: `backend/tests/contract/test_conversation_flow_fidelity.py`

**Interfaces:**
- Consumes: the columns from Task 2.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/contract/test_conversation_flow_fidelity.py`:

```python
@pytest.mark.parametrize("name", FIXTURE_NAMES)
async def test_patch_accepts_every_writable_field(client, name):
    """A PATCH carrying a whole flow must write every field, not a subset.

    This is how the editor saves: it PATCHes the flow object it holds.
    """
    source = load_fixture(name)
    payload = {k: v for k, v in source.items() if k not in SERVER_MANAGED}

    created = await client.post(
        "/create-conversation-flow", headers=AUTH_HEADERS, json={"nodes": []}
    )
    assert created.status_code == 201, created.text
    flow_id = created.json()["conversation_flow_id"]

    patched = await client.patch(
        f"/update-conversation-flow/{flow_id}", headers=AUTH_HEADERS, json=payload
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()

    ignored = sorted(k for k, v in payload.items() if body[k] != v)
    assert not ignored, f"{name}: fields ignored by PATCH: {ignored}"
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd backend && uv run pytest \
  tests/contract/test_conversation_flow_fidelity.py::test_patch_accepts_every_writable_field -v
```

Expected: FAIL with `fields ignored by PATCH: ['begin_tag_display_position', ...]`.

- [ ] **Step 3: Widen `_MUTABLE_FIELDS`**

In `backend/src/arhiteq_api/api/conversation_flows.py`:

```python
_MUTABLE_FIELDS = {
    "global_prompt",
    "nodes",
    "start_node_id",
    "start_speaker",
    "model_choice",
    "tools",
    "default_dynamic_variables",
    "components",
    "notes",
    "kb_config",
    "knowledge_base_ids",
    "mcps",
    "begin_tag_display_position",
    "model_temperature",
    "tool_call_strict_mode",
    "is_transfer_cf",
    "is_transfer_llm",
    "flex_mode",
    "is_published",
}
```

- [ ] **Step 4: Run the full fidelity module**

```bash
cd backend && uv run pytest tests/contract/test_conversation_flow_fidelity.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/arhiteq_api/api/conversation_flows.py \
        backend/tests/contract/test_conversation_flow_fidelity.py
git commit -m "fix(flows): allow updating every conversation flow field"
```

---

### Task 4: Reject agents pointing at a missing or foreign flow

`POST /create-agent` accepts `response_engine.conversation_flow_id` today
(`schemas.py:107`) and never checks it. An agent pointing at another workspace's
flow, or at nothing, must not be creatable.

**Files:**
- Modify: `backend/src/arhiteq_api/api/agents.py:167-186` (`create_agent`)
- Create: `backend/tests/contract/test_flow_agents.py`

**Interfaces:**
- Consumes: `ConversationFlow` from `..models`.
- Produces: `_validate_conversation_flow_id(session, flow_id, workspace_id)` in
  `api/agents.py`, and the test helper `create_flow_agent(client)` in
  `tests/contract/test_flow_agents.py`, reused by Task 6.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/contract/test_flow_agents.py`:

```python
"""Agents whose response engine is a conversation flow."""

from tests.conftest import AUTH_HEADERS, INTERNAL_HEADERS, OTHER_AUTH_HEADERS

NODES = [
    {
        "id": "start",
        "type": "conversation",
        "instruction": {"type": "prompt", "text": "Greet the caller."},
        "edges": [
            {
                "id": "edge-1",
                "transition_condition": {"type": "prompt", "prompt": "Done"},
                "destination_node_id": "bye",
            }
        ],
    },
    {
        "id": "bye",
        "type": "end",
        "instruction": {"type": "static_text", "text": "Goodbye."},
    },
]


async def create_flow(client, headers=AUTH_HEADERS, **overrides):
    resp = await client.post(
        "/create-conversation-flow",
        headers=headers,
        json={"nodes": NODES, "start_speaker": "agent", **overrides},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_flow_agent(client, flow_id=None, headers=AUTH_HEADERS):
    if flow_id is None:
        flow_id = (await create_flow(client, headers))["conversation_flow_id"]
    resp = await client.post(
        "/create-agent",
        headers=headers,
        json={
            "agent_name": "Flow Agent",
            "voice_id": "cartesia-sonic-english",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": flow_id,
            },
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def test_create_agent_with_conversation_flow(client):
    agent = await create_flow_agent(client)
    assert agent["response_engine"]["type"] == "conversation-flow"
    assert agent["response_engine"]["conversation_flow_id"].startswith(
        "conversation_flow_"
    )


async def test_create_agent_rejects_unknown_flow(client):
    resp = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "agent_name": "Bad",
            "voice_id": "cartesia-sonic-english",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": "conversation_flow_does_not_exist",
            },
        },
    )
    assert resp.status_code == 404, resp.text


async def test_create_agent_rejects_another_workspaces_flow(client):
    foreign = await create_flow(client, headers=OTHER_AUTH_HEADERS)
    resp = await client.post(
        "/create-agent",
        headers=AUTH_HEADERS,
        json={
            "agent_name": "Cross tenant",
            "voice_id": "cartesia-sonic-english",
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": foreign["conversation_flow_id"],
            },
        },
    )
    assert resp.status_code == 404, resp.text
```

- [ ] **Step 2: Run it and confirm the rejection tests fail**

```bash
cd backend && uv run pytest tests/contract/test_flow_agents.py -v
```

Expected: `test_create_agent_with_conversation_flow` PASSES; the two rejection
tests FAIL with `assert 200 == 404` — the agent is created against a flow that
does not exist or belongs elsewhere.

- [ ] **Step 3: Add the validator**

In `backend/src/arhiteq_api/api/agents.py`, add `ConversationFlow` to the existing
`from ..models import ...` line, then add this helper next to
`_validate_folder_id`:

```python
async def _validate_conversation_flow_id(
    session: AsyncSession, flow_id: str | None, workspace_id: str
) -> None:
    """A flow-backed agent must point at a flow this workspace owns.

    404 rather than 403: a foreign id must not be distinguishable from a
    missing one, or the error leaks whether an id exists in another tenant.
    """
    if not flow_id:
        return
    flow = await session.get(ConversationFlow, flow_id)
    if flow is None or flow.workspace_id != workspace_id:
        raise HTTPException(404, detail="Conversation flow not found")
```

- [ ] **Step 4: Call it from `create_agent`**

In `create_agent`, directly after the existing `_validate_folder_id` call:

```python
    await _validate_conversation_flow_id(
        session, body.response_engine.conversation_flow_id, api_key.workspace_id
    )
```

- [ ] **Step 5: Run the tests**

```bash
cd backend && uv run pytest tests/contract/test_flow_agents.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Guard the update path too**

`PATCH /update-agent` can re-point `response_engine`. Add this test to
`backend/tests/contract/test_flow_agents.py`:

```python
async def test_update_agent_rejects_another_workspaces_flow(client):
    agent = await create_flow_agent(client)
    foreign = await create_flow(client, headers=OTHER_AUTH_HEADERS)
    resp = await client.patch(
        f"/update-agent/{agent['agent_id']}",
        headers=AUTH_HEADERS,
        json={
            "response_engine": {
                "type": "conversation-flow",
                "conversation_flow_id": foreign["conversation_flow_id"],
            }
        },
    )
    assert resp.status_code == 404, resp.text
```

Run it:

```bash
cd backend && uv run pytest \
  tests/contract/test_flow_agents.py::test_update_agent_rejects_another_workspaces_flow -v
```

Expected: FAIL (the patch succeeds with 200).

- [ ] **Step 7: Validate in `update_agent`**

Find `async def update_agent` in `backend/src/arhiteq_api/api/agents.py`. After the
request payload is read and before the fields are applied, insert:

```python
    engine = payload.get("response_engine")
    if isinstance(engine, dict):
        await _validate_conversation_flow_id(
            session, engine.get("conversation_flow_id"), api_key.workspace_id
        )
```

If `update_agent` reads its body through a Pydantic model rather than
`await request.json()`, read the equivalent attribute instead — match the
surrounding style, do not change how the handler parses its body.

- [ ] **Step 8: Run the module and the agent suite**

```bash
cd backend && uv run pytest tests/contract/test_flow_agents.py \
  tests/contract/test_crud_resources.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend/src/arhiteq_api/api/agents.py backend/tests/contract/test_flow_agents.py
git commit -m "feat(flows): validate conversation_flow_id on agent create and update"
```

---

### Task 5: Snapshot the flow at publish, serve it at resolve

**Files:**
- Modify: `backend/src/arhiteq_api/models.py:335-354` (`AgentVersion`)
- Modify: `backend/src/arhiteq_api/main.py` (`_COLUMN_BACKFILLS`)
- Modify: `backend/src/arhiteq_api/services/versions.py`
- Modify: `backend/tests/contract/test_flow_agents.py`

**Interfaces:**
- Consumes: `create_flow_agent(client)` and `create_flow(client)` from Task 4.
- Produces, in `services/versions.py`:
  - `_FLOW_EXCLUDED: frozenset[str]`
  - `async def _load_flow(session, agent) -> ConversationFlow | None`
  - `async def resolve_with_flow(session, agent, ref=LATEST_PUBLISHED, *, strict=True)
     -> tuple[Agent, RetellLLM | None, ConversationFlow | None, int]`

  `resolve()` keeps its existing 3-tuple signature and all ten of its current call
  sites are untouched. Task 7 uses `resolve_with_flow`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/contract/test_flow_agents.py`:

```python
async def test_published_version_freezes_the_flow(client):
    """Editing a draft flow must not change what a published version serves."""
    agent = await create_flow_agent(client)
    agent_id = agent["agent_id"]
    flow_id = agent["response_engine"]["conversation_flow_id"]

    published = await client.post(
        f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={}
    )
    assert published.status_code == 200, published.text
    version = published.json()["version"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "REWRITTEN AFTER PUBLISH"},
    )
    assert edited.status_code == 200, edited.text

    config = await client.get(
        f"/internal/agents/{agent_id}/config", headers=INTERNAL_HEADERS
    )
    assert config.status_code == 200, config.text
    served = config.json()["conversation_flow"]
    assert served is not None
    assert served["global_prompt"] != "REWRITTEN AFTER PUBLISH", (
        f"published version {version} served the edited draft flow"
    )
```

`INTERNAL_HEADERS` already exists in `backend/tests/conftest.py:50` — widen this
module's import line rather than redefining it:

```python
from tests.conftest import AUTH_HEADERS, INTERNAL_HEADERS, OTHER_AUTH_HEADERS
```

`POST /publish-agent/{agent_id}` with `json={}` is the publish route
(`api/agents.py:379`) and it returns `agent_to_dict`, so `["version"]` is the
published version number.

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd backend && uv run pytest \
  tests/contract/test_flow_agents.py::test_published_version_freezes_the_flow -v
```

Expected: FAIL with `KeyError: 'conversation_flow'` — the internal config does not
carry a flow yet. That is the correct first failure; Task 7 finishes this test.

- [ ] **Step 3: Add the snapshot column**

In `backend/src/arhiteq_api/models.py`, in `class AgentVersion`, directly after
`llm_snapshot`:

```python
    # Flow-backed agents freeze their whole graph here, same contract as
    # llm_snapshot: NULL while the version is a draft.
    flow_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
```

And in `_COLUMN_BACKFILLS` in `backend/src/arhiteq_api/main.py`:

```python
    ("agent_versions", "flow_snapshot", "JSON"),
```

- [ ] **Step 4: Teach `services/versions.py` about flows**

Add `ConversationFlow` to the module's `from ..models import ...` line. Add the
exclusion set next to `_LLM_EXCLUDED`:

```python
_FLOW_EXCLUDED = frozenset(
    {"conversation_flow_id", "workspace_id", "version", "last_modification_timestamp",
     "created_at_ms"}
)
```

Add the loader next to `_load_llm`:

```python
async def _load_flow(session: AsyncSession, agent: Agent) -> ConversationFlow | None:
    flow_id = (agent.response_engine or {}).get("conversation_flow_id")
    if not flow_id:
        return None
    return await session.get(ConversationFlow, flow_id)
```

- [ ] **Step 5: Snapshot the flow wherever the LLM is snapshotted**

There are three sites, all already calling `_snapshot(llm, _LLM_EXCLUDED)`. At
each, add the flow line beside it.

In `ensure_seeded` (`versions.py:129-130`) and in `record_initial`
(`versions.py:154-155`), inside the `AgentVersion(...)` constructor:

```python
            llm_snapshot=_snapshot(llm, _LLM_EXCLUDED) if llm is not None else None,
            flow_snapshot=_snapshot(flow, _FLOW_EXCLUDED) if flow is not None else None,
```

Each of those two functions loads the LLM with `llm = await _load_llm(session, agent)`;
add `flow = await _load_flow(session, agent)` immediately after it.

In `publish` (`versions.py:222-224`):

```python
        llm = await _load_llm(session, agent)
        flow = await _load_flow(session, agent)
        row.agent_snapshot = _snapshot(agent, _AGENT_EXCLUDED)
        row.llm_snapshot = _snapshot(llm, _LLM_EXCLUDED) if llm is not None else None
        row.flow_snapshot = (
            _snapshot(flow, _FLOW_EXCLUDED) if flow is not None else None
        )
```

- [ ] **Step 6: Add `resolve_with_flow`**

`resolve()` has ten call sites and only two need the flow, so leave its signature
alone and add a sibling. Immediately after `resolve()` in `versions.py`:

```python
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
    """
    pinned, llm, version = await resolve(session, agent, ref, strict=strict)
    flow = await _load_flow(session, pinned)
    if flow is None or version == agent.version:
        # Live rows (a draft, or an agent with no history) serve the live flow.
        return pinned, llm, flow, version
    row = await _get(session, agent.agent_id, version)
    if row is None or row.flow_snapshot is None:
        return pinned, llm, flow, version
    return pinned, llm, _detach(flow, row.flow_snapshot, _FLOW_EXCLUDED), version
```

- [ ] **Step 7: Verify the snapshot is written**

```bash
cd backend && uv run pytest tests/contract/test_agent_versions.py -v
```

Expected: all pass — the existing version suite must be unaffected. The new flow
test still fails on the missing `conversation_flow` key; Task 7 closes it.

- [ ] **Step 8: Commit**

```bash
git add backend/src/arhiteq_api/models.py backend/src/arhiteq_api/main.py \
        backend/src/arhiteq_api/services/versions.py
git commit -m "feat(flows): freeze the conversation flow into published agent versions"
```

---

### Task 6: Restore flows on branch and discard

`branch` (rollback) and `discard` write a version's snapshot back onto the live
rows via `_restore_config`. That handles the agent and the LLM; a flow-backed
agent currently rolls back its prompt but keeps the newer graph.

**Files:**
- Modify: `backend/src/arhiteq_api/services/versions.py:295-333` (`_restore_config`)
- Modify: `backend/tests/contract/test_flow_agents.py`

**Interfaces:**
- Consumes: `_load_flow`, `_FLOW_EXCLUDED`, `_restore` from Task 5.
- Produces: `async def agents_using_flow(session, workspace_id, flow_id) -> list[Agent]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/contract/test_flow_agents.py`:

```python
async def test_branching_restores_the_flow_graph(client):
    """Rolling back to an old version must roll the graph back with it."""
    agent = await create_flow_agent(client)
    agent_id = agent["agent_id"]
    flow_id = agent["response_engine"]["conversation_flow_id"]

    first = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert first.status_code == 200, first.text
    original_version = first.json()["version"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "V2 PROMPT"},
    )
    assert edited.status_code == 200, edited.text
    second = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert second.status_code == 200, second.text

    branched = await client.post(
        f"/create-agent-version/{agent_id}",
        headers=AUTH_HEADERS,
        json={"base_version": original_version},
    )
    assert branched.status_code in (200, 201), branched.text

    flow = await client.get(
        f"/get-conversation-flow/{flow_id}", headers=AUTH_HEADERS
    )
    assert flow.status_code == 200, flow.text
    assert flow.json()["global_prompt"] != "V2 PROMPT", (
        "branching from the original version left the V2 graph live"
    )
```

Both routes are confirmed: `POST /publish-agent/{agent_id}`
(`api/agents.py:379`) and `POST /create-agent-version/{agent_id}`, which returns
201 (`api/agents.py:331`).

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd backend && uv run pytest \
  tests/contract/test_flow_agents.py::test_branching_restores_the_flow_graph -v
```

Expected: FAIL — the live flow still reads `"V2 PROMPT"`.

- [ ] **Step 3: Add the sharing query**

Next to `agents_using_llm` in `versions.py`:

```python
async def agents_using_flow(
    session: AsyncSession, workspace_id: str, flow_id: str
) -> list[Agent]:
    """Agents in this workspace whose response engine is `flow_id`.

    Filtered in Python for the same reason agents_using_llm is: a JSON
    predicate would need dialect-specific SQL for SQLite and Postgres both.
    """
    agents = await session.scalars(select(Agent).where(Agent.workspace_id == workspace_id))
    return [
        a for a in agents if (a.response_engine or {}).get("conversation_flow_id") == flow_id
    ]
```

- [ ] **Step 4: Restore the flow in `_restore_config`**

At the end of `_restore_config`, after the existing LLM restore block:

```python
    flow = await _load_flow(session, agent)
    if flow is None:
        return
    snapshot = version.flow_snapshot or {}
    if not snapshot or all(
        getattr(flow, field, None) == value for field, value in snapshot.items()
    ):
        return  # already the restored graph — nothing to write, nothing to fork
    # A flow shared by several agents is one config for all of them, so
    # restoring in place would rewrite the others' graph. Fork, exactly as the
    # LLM path above does.
    sharers = await agents_using_flow(session, agent.workspace_id, flow.conversation_flow_id)
    if len(sharers) > 1:
        copy = ConversationFlow(workspace_id=agent.workspace_id)
        for column in ConversationFlow.__table__.columns:
            if column.name not in _FLOW_EXCLUDED:
                setattr(copy, column.name, getattr(flow, column.name))
        session.add(copy)
        await session.flush()
        agent.response_engine = {
            **(agent.response_engine or {}),
            "conversation_flow_id": copy.conversation_flow_id,
        }
        flow = copy
    _restore(flow, version.flow_snapshot, _FLOW_EXCLUDED)
```

Read the existing LLM fork block just above (`versions.py:307-331`) first and
mirror its structure — if it differs from the sketch here, follow the existing
code, not this snippet.

- [ ] **Step 5: Run the flow and version suites**

```bash
cd backend && uv run pytest tests/contract/test_flow_agents.py \
  tests/contract/test_agent_versions.py tests/contract/test_concurrency_and_versions.py -v
```

Expected: everything passes except
`test_published_version_freezes_the_flow`, still waiting on Task 7.

- [ ] **Step 6: Commit**

```bash
git add backend/src/arhiteq_api/services/versions.py backend/tests/contract/test_flow_agents.py
git commit -m "feat(flows): restore the flow graph when branching or discarding a version"
```

---

### Task 7: Expose the resolved flow over the internal API

**Files:**
- Modify: `backend/src/arhiteq_api/api/internal.py:36-61` (`_call_config`)
- Modify: `backend/src/arhiteq_api/api/internal.py:72-96` (`get_agent_config`)

**Interfaces:**
- Consumes: `versions.resolve_with_flow` (Task 5),
  `schemas_extra.conversation_flow_to_dict`.
- Produces: both internal config endpoints gain a `conversation_flow` key —
  the serialized flow, or `null` for a single-prompt agent. Plan 2's worker
  reads this key.

- [ ] **Step 1: Import the serializer**

At the top of `backend/src/arhiteq_api/api/internal.py`, add to the existing
imports:

```python
from ..schemas_extra import conversation_flow_to_dict
```

- [ ] **Step 2: Return the flow from `_call_config`**

Replace the resolve line and add one dict key:

```python
    agent, llm, flow, _ = await versions.resolve_with_flow(
        session, live, call.agent_version, strict=False
    )
```

and inside the returned dict, after the `"llm"` entry:

```python
        "conversation_flow": conversation_flow_to_dict(flow) if flow is not None else None,
```

- [ ] **Step 3: Return the flow from `get_agent_config`**

```python
    agent, llm, flow, _ = await versions.resolve_with_flow(session, live)
    return {
        "agent": agent_to_dict(agent),
        "llm": llm_to_dict(llm) if llm is not None else None,
        "conversation_flow": conversation_flow_to_dict(flow) if flow is not None else None,
    }
```

- [ ] **Step 4: Run the flow suite — the freeze test must now pass**

```bash
cd backend && uv run pytest tests/contract/test_flow_agents.py -v
```

Expected: all pass, including `test_published_version_freezes_the_flow`.

- [ ] **Step 5: Run the whole backend suite**

```bash
cd backend && uv run pytest
```

Expected: all pass. `conversation_flow` is an added key, and the contract permits
extra fields, so no existing internal-API assertion can break.

- [ ] **Step 6: Commit**

```bash
git add backend/src/arhiteq_api/api/internal.py
git commit -m "feat(flows): serve the resolved conversation flow to the worker"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/API_COVERAGE.md:15`
- Modify: `docs/AGENT_VERSIONING.md`
- Modify: `docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md`
- Modify: `backend/src/arhiteq_api/services/versions.py:14` (module docstring)

- [ ] **Step 1: Update the coverage matrix**

In `docs/API_COVERAGE.md`, replace the Notes cell of the Conversation flow row —
currently "CRUD full; flow *execution* by the voice worker is single-prompt only
for now" — with:

```
CRUD full and field-lossless (round-trip proven against real Retell fixtures in `backend/tests/fixtures/retell_flows/`); flow *execution* by the voice worker not yet implemented
```

- [ ] **Step 2: Correct the versioning docs**

In `docs/AGENT_VERSIONING.md`, find the statement that only voice agents are
versioned and that conversation flows keep a plain counter, and add:

```markdown
A flow-backed voice agent freezes its whole graph at publish: the version's
`flow_snapshot` holds the conversation flow's columns, exactly as `llm_snapshot`
holds the Retell LLM's. Editing a draft flow can never change what a published
version serves. The `conversation_flows.version` counter still exists and still
bumps on every PATCH, but it is bookkeeping — it is not what a call resolves
against.
```

- [ ] **Step 3: Correct the same claim in the module docstring**

In `backend/src/arhiteq_api/services/versions.py`, replace the docstring line
"Only voice agents are versioned. Chat agents and conversation flows keep their
plain `version` counter." with:

```
Only voice agents are versioned. Chat agents keep their plain `version` counter.
A flow-backed voice agent's graph is frozen into the version's `flow_snapshot`,
so the flow's own counter is bookkeeping rather than what a call resolves against.
```

- [x] **Step 4: State in the spec where node validation lives** — done directly
  by the controller, not this task. This step originally quoted a paragraph to
  *replace*, but that paragraph was never written into the spec file, so the
  implementer correctly refused to fabricate the edit. The spec now carries a
  "Where node validation lives" subsection stating the arrangement positively.

- [ ] **Step 5: Run the full suite once more and commit**

```bash
cd backend && uv run pytest
git add docs backend/src/arhiteq_api/services/versions.py
git commit -m "docs(flows): document lossless flow storage and version snapshots"
```

---

### Task 9: Open the PR

- [ ] **Step 1: Verify the branch is green from a clean state**

```bash
cd backend && uv run pytest
cd .. && pre-commit run --all-files
```

Expected: both clean. Report any failure rather than working around it.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/conversation-flow-agents
gh pr create --title "feat(flows): full-fidelity conversation flow storage" --body "$(cat <<'EOF'
Implements plan 1 of 3 from `docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md`.

**Fixes a real bug:** the API silently dropped 12 of the 19 fields a live Retell
conversation flow carries. Importing a flow via `scripts/migrate_retell_agent.py`
lost its subflows, canvas layout, knowledge-base config and notes. Three real
(sanitized) flows are committed as fixtures and a round-trip test now proves
nothing is dropped.

**Adds versioning:** a flow-backed agent freezes its graph into
`agent_versions.flow_snapshot` at publish, so editing a draft flow can no longer
change what a live call runs. Branch and discard restore the graph, forking a
shared flow rather than rewriting another agent's config.

**Adds the worker's data path:** the internal config endpoints now return a
`conversation_flow` object. Nothing consumes it yet.

No user-visible change: the "Conversational flow" card in the create modal stays
disabled until the editor lands in plan 3.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01KYq5zQXaqNhgY39WnXhBZm
EOF
)"
```

---

## What this plan does NOT do

Stated so a reviewer does not look for them:

- No flow **execution**. The worker still runs single-prompt only; a flow-backed
  agent will start a call and behave as if it had no prompt. Plan 2.
- No **editor**. Plan 3.
- No node-type validation anywhere in the backend, by design (see
  [Correction to the spec](#correction-to-the-spec)).
- No `component`-node, `press_digit`, `agent_swap`, `sms`, `mcp` or `code`
  support — those graphs store fine, they just will not run.
