# Conversation Flow Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the dashboard a node-graph editor for conversation-flow agents, and enable creating one — the third and final PR of `docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md`.

**Architecture:** `/agents/[id]` keeps its header, versioning, publish and settings rail; when the agent's `response_engine.type` is `conversation-flow` the prompt column is replaced by a three-pane `<FlowEditor>` (palette · canvas · settings). All graph logic lives in two pure, React-free modules (`flowModel.ts`, `flowGraph.ts`) tested with `bun test`; React Flow renders what they produce. The editor mutates a deep copy of the server's flow JSON and never reconstructs it from a typed model, so unknown Retell keys survive by construction.

**Tech Stack:** Next 16.2.11 · React 19.2 · TypeScript 6 · Tailwind 4 · bun (package manager *and* test runner) · `@xyflow/react` 12.11.2 (new dependency) · FastAPI/SQLAlchemy backend (one small additive change).

## Global Constraints

- **`main` is protected.** PR only, squash merge, conventional-commit PR title. Branch for this plan: `feat/conversation-flow-editor`.
- **The wire contract is frozen.** Extra fields are fine; renames and drops are not. Anything this plan adds to a backend response is additive.
- **Fidelity rule.** The editor deep-copies the server's flow JSON, mutates the copy, and sends it back. It must never rebuild the object from a typed model — unknown keys (`flex_mode`, `is_transfer_cf`, whatever Retell ships next) survive only by construction. Every TypeScript type for flow content is an *open* shape (`& Record<string, unknown>`), never a closed one.
- **Read `node_modules/next/dist/docs/` before writing Next-specific code.** `frontend/AGENTS.md` is explicit: this is not the Next.js in your training data. Relevant here: `01-app/03-api-reference` for anything app-router or CSS related.
- **Fixtures are read-only.** `backend/tests/fixtures/retell_flows/*.json` are sanitized real Retell captures and the shared schema authority for backend, worker and now frontend tests. **Never edit or regenerate them.** If a test disagrees with a fixture, the test is wrong.
- **Frontend commands:** `cd frontend && bun install`, `bun run build`, `bun run lint`, `bun test`. Backend: `cd backend && uv run pytest`.
- **pre-commit hooks run on commit** (gitleaks, ruff check+format, backend pytest, eslint). The backend pytest hook id is `pytest-backend`. Two `EXE001` failures under `backend/scripts/` are **pre-existing** — do not "fix" them, and never let a ruff autofix rewrite `backend/scripts/generate_voice_previews.py`; revert it if it appears in your diff.
- **Supported node types are exactly seven:** `conversation`, `subagent`, `branch`, `function`, `transfer_call`, `end`, `extract_dynamic_variables`. The worker rejects a graph containing anything else *at call start*, so the palette must not offer an eighth. (`worker/src/arhiteq_worker/flow.py:SUPPORTED_NODE_TYPES`.)
- **Five edge shapes**, each with different runtime meaning: `edges[]` (list, conditional), `else_edge` (guaranteed fallback), `edge` (single failure edge, on `transfer_call`), `always_edge` (unconditional, fires on the next user turn), `skip_response_edge` (speak the node's line, then advance **without** waiting for the caller).
- **Two condition forms:** `transition_condition.type` is `"prompt"` (`{type, prompt}`) or `"equation"` (`{type, equations: [{left, operator, right}], operator: "&&" | "||"}`). Equation operators: `>` `<` `==` `!=` `CONTAINS` `NOT CONTAINS` `exists`.
- **Existing UI kit only** — `@/components/ui/{Field,Select,Toggle,Button,Accordion,Modal,Slider,RadioRow,Tooltip}`, `cn` from `@/lib/utils`, `lucide-react` icons. Do not introduce a second styling idiom. Match the surrounding Tailwind vocabulary (`text-[13px]`, `border-line`, `bg-card`, `text-sub`, `text-ink`, `text-faint`).
- **No new dependency other than `@xyflow/react`.** It pulls `@xyflow/system`, `zustand` and `classcat` transitively; that is accepted (spec § Editor). `bun test` is built into bun — do **not** add vitest/jest.

---

## File Structure

**New — `frontend/src/components/flow/`** (all client-side):

| File | Responsibility |
|---|---|
| `flowModel.ts` | The flow document: open types, edge enumeration, the reducer and its actions, id minting, the seed graph, known-variable discovery. **Pure TS, no React, no imports from `@xyflow/react`.** |
| `flowGraph.ts` | Adapter both ways between the flow JSON and React Flow's `nodes` / `edges` arrays. **Pure TS**; may import types (not components) from `@xyflow/react`. |
| `FlowEditor.tsx` | The three-pane shell and the `<ReactFlow>` canvas. |
| `NodePalette.tsx` | Left pane: the seven node types, drag-or-click to add. |
| `nodes/nodeMeta.ts` | Per-type icon, label, accent and subtitle renderer. |
| `nodes/NodeShell.tsx` | Shared node chrome: title bar, body slot, source/target handles. |
| `nodes/FlowNode.tsx` | The single React Flow node component, driven by `nodeMeta`. |
| `nodes/NoteNode.tsx` | A `notes[]` sticky. |
| `settings/NodeSettings.tsx` | Right pane dispatcher: current node → its editor. |
| `settings/ConversationSettings.tsx` | `conversation` + `subagent` (identical field set). |
| `settings/BranchSettings.tsx`, `FunctionSettings.tsx`, `TransferSettings.tsx`, `EndSettings.tsx`, `ExtractSettings.tsx` | One per remaining type. |
| `settings/EdgeList.tsx` | A node's edges, grouped by shape, each opening a `ConditionEditor`. |
| `settings/ConditionEditor.tsx` | Switches an edge between `prompt` and `equation`. |
| `settings/EquationBuilder.tsx` | Row-per-equation builder with the `&&` / `\|\|` combinator. |
| `settings/GlobalSettings.tsx` | Flow-level `global_prompt`, `model_choice`, `model_temperature`, `start_speaker`. |

**New tests — `frontend/src/components/flow/__tests__/`**: `flowModel.test.ts`, `flowGraph.test.ts`.

**Modified:**

| File | Change |
|---|---|
| `backend/src/arhiteq_api/api/agents.py` | `get_agent_version` returns the version's frozen `conversation_flow`. |
| `backend/tests/test_agent_versions.py` | Regression test for the above. |
| `frontend/package.json` | `@xyflow/react` dependency; `"test": "bun test"` script. |
| `frontend/src/lib/api.ts` | `RawConversationFlow`, `getConversationFlow`, `updateConversationFlow`; `getAgentDetail` returns the flow; `RawAgentVersion.conversation_flow`. |
| `frontend/src/lib/mock.ts` | Demo-mode flow so `DEMO_MODE` doesn't 404 on the new endpoints. |
| `frontend/src/app/agents/[id]/page.tsx` | Branch on engine type; flow draft + autosave; pass the flow down. |
| `frontend/src/components/editor/sections/FunctionsSection.tsx`, `KnowledgeBaseSection.tsx`, `McpSection.tsx` | Accept flow-shaped values so the rail edits the flow's own fields. |
| `frontend/src/components/agents/CreateAgentModal.tsx` | Enable the "Conversational flow" card. |
| `frontend/src/components/simulation/SimulationTab.tsx` | Render `TestPanel` for flow agents instead of blocking the whole tab. |
| `docs/UI_INVENTORY.md`, `docs/API_COVERAGE.md`, `docs/ARCHITECTURE.md` | Record the editor. |

---

### Task 1: The editor can load the right graph

The editor cannot start until two gaps are closed. First, `GET /get-agent-version/{agent_id}/{version}` returns `response_engine_config` (the LLM) but **no flow** — it calls `versions.resolve()` (3-tuple) rather than `versions.resolve_with_flow()` (4-tuple). Viewing V3 of a flow agent would therefore show today's graph labelled V3, which is precisely what plan 1 existed to prevent. Second, `api.getAgentDetail` fetches the LLM but never the flow.

**Files:**
- Modify: `backend/src/arhiteq_api/api/agents.py` (`get_agent_version`, around line 361)
- Modify: `backend/tests/contract/test_flow_agents.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/mock.ts`

**Interfaces:**
- Produces: `RawConversationFlow`, `api.getConversationFlow(id)`, `api.updateConversationFlow(id, body)`, `AgentDetail.flow: RawConversationFlow | null`, `RawAgentVersion.conversation_flow?: RawConversationFlow | null`. Every later task consumes these.

- [ ] **Step 1: Write the failing backend test**

Append to `backend/tests/contract/test_flow_agents.py`. That module already has the two helpers this needs — `create_flow(client, **overrides)` and `create_flow_agent(client, flow_id=...)` — and imports `AUTH_HEADERS` from `tests.conftest`. The existing `test_published_version_freezes_the_flow` proves the freeze through the **worker's** route (`/internal/calls/{call_id}/config`); this proves it through the **dashboard's**, which is a different code path and currently has no flow at all.

```python
async def test_get_agent_version_returns_the_frozen_graph(client):
    """The dashboard's version route must freeze the graph like the worker's.

    `test_published_version_freezes_the_flow` covers /internal/calls/.../config.
    This covers /get-agent-version, which the editor reads when you select an
    older version — it used to call `versions.resolve()` (no flow at all), so
    the canvas would render TODAY's graph under a "Viewing V1 — published
    versions are immutable" banner.
    """
    flow = await create_flow(client, global_prompt="ORIGINAL")
    flow_id = flow["conversation_flow_id"]
    agent = await create_flow_agent(client, flow_id=flow_id)
    agent_id = agent["agent_id"]

    published = await client.post(f"/publish-agent/{agent_id}", headers=AUTH_HEADERS, json={})
    assert published.status_code == 200, published.text
    version = published.json()["version"]

    edited = await client.patch(
        f"/update-conversation-flow/{flow_id}",
        headers=AUTH_HEADERS,
        json={"global_prompt": "REWRITTEN AFTER PUBLISH"},
    )
    assert edited.status_code == 200, edited.text

    got = await client.get(f"/get-agent-version/{agent_id}/{version}", headers=AUTH_HEADERS)
    assert got.status_code == 200, got.text
    served = got.json()["conversation_flow"]
    assert served is not None
    assert served["global_prompt"] == "ORIGINAL"
    assert served["nodes"] == NODES


async def test_get_agent_version_flow_is_null_for_a_prompt_agent(client):
    """The key is always present, so the client never feature-detects."""
    got = await client.get(f"/get-agent-version/{AGENT_ID}/0", headers=AUTH_HEADERS)
    assert got.status_code == 200, got.text
    assert got.json()["conversation_flow"] is None
    assert got.json()["response_engine_config"] is not None
```

Add `AGENT_ID` to the module's existing `from tests.conftest import ...` line — it is the conftest's seeded prompt-based agent.

- [ ] **Step 2: Run it and watch it fail**

```bash
cd backend && uv run pytest tests/contract/test_flow_agents.py -k get_agent_version -v
```
Expected: FAIL with `KeyError: 'conversation_flow'`.

- [ ] **Step 3: Return the frozen flow**

In `backend/src/arhiteq_api/api/agents.py`, `get_agent_version`:

```python
    pinned, llm, flow, _ = await versions.resolve_with_flow(session, agent, version)
    return {
        **versions.to_dict(row, agent_to_dict(pinned), live_version=agent.published_version),
        # Arhiteq extras: the prompt/tools *or* the graph this version runs, so
        # the editor can show an older version without a second version-aware
        # endpoint per engine type. Exactly one is non-null for any given agent.
        "response_engine_config": llm_to_dict(llm) if llm is not None else None,
        "conversation_flow": conversation_flow_to_dict(flow) if flow is not None else None,
    }
```

Import `conversation_flow_to_dict` from `arhiteq_api.schemas_extra` alongside the existing `llm_to_dict` import. `resolve_with_flow` is already exported from `services/versions.py` (`api/internal.py` uses it twice).

- [ ] **Step 4: Run the backend suite**

```bash
cd backend && uv run pytest -q
```
Expected: PASS, and the count is 2 higher than before (537).

- [ ] **Step 5: Add the TypeScript client surface**

In `frontend/src/lib/api.ts`. Place `RawConversationFlow` next to `RawLlm`. **The type is open on purpose** — see Global Constraints:

```typescript
/**
 * A conversation flow exactly as the control plane serves it.
 *
 * Deliberately OPEN (`Record<string, unknown>` tail): the editor round-trips
 * this object, and a closed type would invite rebuilding it field-by-field,
 * which is how Retell keys we do not model yet (`flex_mode`, `is_transfer_cf`,
 * next month's addition) get silently dropped. Nodes and edges are
 * `Record<string, unknown>` for the same reason — `flowModel.ts` narrows what
 * it reads without ever asserting what is there.
 */
export type RawConversationFlow = {
  conversation_flow_id: string;
  version: number;
  global_prompt?: string | null;
  nodes?: Record<string, unknown>[] | null;
  start_node_id?: string | null;
  start_speaker?: string | null;
  model_choice?: Record<string, unknown> | null;
  model_temperature?: number | null;
  tools?: Record<string, unknown>[] | null;
  default_dynamic_variables?: Record<string, string> | null;
  components?: Record<string, unknown>[] | null;
  notes?: Record<string, unknown>[] | null;
  kb_config?: Record<string, unknown> | null;
  knowledge_base_ids?: string[] | null;
  mcps?: Record<string, unknown>[] | null;
  begin_tag_display_position?: { x: number; y: number } | null;
} & Record<string, unknown>;
```

Add to the `api` object, next to the existing `createConversationFlow`:

```typescript
  getConversationFlow: (flowId: string) =>
    request<RawConversationFlow>(`/get-conversation-flow/${encodeURIComponent(flowId)}`),

  updateConversationFlow: (flowId: string, body: Partial<RawConversationFlow>) =>
    request<RawConversationFlow>(
      `/update-conversation-flow/${encodeURIComponent(flowId)}`,
      patch(body),
    ),
```

Extend `AgentDetail` with `flow: RawConversationFlow | null` and fetch it in `getAgentDetail` (around line 910), mirroring the LLM fetch:

```typescript
    const engine = agent.response_engine;
    const llm = engine?.llm_id
      ? await request<RawLlm>(`/get-retell-llm/${encodeURIComponent(engine.llm_id)}`)
      : null;
    // Exactly one engine is ever populated: `llm_id` on a single-prompt agent,
    // `conversation_flow_id` on a flow-backed one.
    const flow = engine?.conversation_flow_id
      ? await request<RawConversationFlow>(
          `/get-conversation-flow/${encodeURIComponent(engine.conversation_flow_id)}`,
        )
      : null;
    return { agent, llm, flow, is_chat: isChat };
```

Add `conversation_flow?: RawConversationFlow | null;` to `RawAgentVersion`.

- [ ] **Step 6: Teach demo mode about flows**

`DEMO_MODE` routes every request through `mock.ts:demoResponse`, and `mock.ts` already mints `conversation_flow_id: flow_demo_<suffix>` for three agents — so without this the editor 404s in the demo build. Add a handler for `/get-conversation-flow/:id` returning a small three-node graph (start `conversation` → `branch` → `end`), and make `/update-conversation-flow/:id` echo the merged body back. Follow the routing style `demoResponse` already uses for other paths; read it before writing.

- [ ] **Step 7: Typecheck and commit**

```bash
cd frontend && bun run lint && bun run build
```
Expected: PASS.

```bash
git add backend/src/arhiteq_api/api/agents.py backend/tests/test_agent_versions.py \
        frontend/src/lib/api.ts frontend/src/lib/mock.ts
git commit -m "feat(flows): serve a version's frozen graph and fetch it in the dashboard"
```

---

### Task 2: `flowModel.ts` — the document, the reducer, the fidelity guard

The single source of truth for what a flow *is* on the client. Pure TypeScript: no React, no `@xyflow/react`. This is what `bun test` exercises, and the fidelity test here is the regression guard that stops the editor silently dropping Retell keys.

**Files:**
- Create: `frontend/src/components/flow/flowModel.ts`
- Create: `frontend/src/components/flow/__tests__/flowModel.test.ts`
- Modify: `frontend/package.json` (add `"test": "bun test"`)

**Interfaces:**
- Consumes: `RawConversationFlow` (Task 1).
- Produces — every later task uses these exact names:
  - `type FlowNode = { id: string; type: string } & Record<string, unknown>`
  - `type FlowEdge = { id: string; destination_node_id?: string; transition_condition?: TransitionCondition } & Record<string, unknown>`
  - `type EdgeShape = "edges" | "else_edge" | "edge" | "always_edge" | "skip_response_edge"`
  - `const EDGE_SHAPES: readonly EdgeShape[]`
  - `const NODE_TYPES: readonly string[]` (the supported seven)
  - `function iterNodeEdges(node: FlowNode): { shape: EdgeShape; edge: FlowEdge; index: number }[]`
  - `function knownVariables(flow: RawConversationFlow): string[]`
  - `function seedFlow(): Partial<RawConversationFlow>`
  - `function newNodeId(type: string): string`, `function newEdgeId(shape: EdgeShape): string`
  - `type FlowAction` and `function flowReducer(flow: RawConversationFlow, action: FlowAction): RawConversationFlow`

- [ ] **Step 1: Add the test script**

In `frontend/package.json`, add to `"scripts"`: `"test": "bun test"`. bun's runner is built in and Jest-compatible; **no devDependency is added.**

- [ ] **Step 2: Write the failing tests**

`frontend/src/components/flow/__tests__/flowModel.test.ts`:

```typescript
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  EDGE_SHAPES,
  flowReducer,
  iterNodeEdges,
  knownVariables,
  newNodeId,
  seedFlow,
  type FlowNode,
} from "../flowModel";
import type { RawConversationFlow } from "@/lib/api";

// The sanitized real-Retell captures are the shared schema authority for the
// backend, the worker AND this editor. Reading them from here is deliberate:
// if the editor drifts from what Retell actually sends, these fail.
const FIXTURES = join(import.meta.dir, "../../../../../backend/tests/fixtures/retell_flows");
const load = (name: string): RawConversationFlow =>
  JSON.parse(readFileSync(join(FIXTURES, name), "utf8"));

const NAMES = ["prior_auth_hotline.json", "clara_outbound.json", "identity_verify_transfer.json"];

describe("fidelity", () => {
  test.each(NAMES)("%s survives a no-op edit byte for byte", (name) => {
    const flow = load(name);
    const before = JSON.stringify(flow);
    // Rename a node to X and back: two real reducer passes, not a clone.
    const nodeId = (flow.nodes as FlowNode[])[0].id;
    const renamed = flowReducer(flow, {
      type: "patchNode",
      nodeId,
      patch: { name: "___temp___" },
    });
    const restored = flowReducer(renamed, {
      type: "patchNode",
      nodeId,
      patch: { name: (flow.nodes as FlowNode[])[0].name },
    });
    expect(JSON.stringify(restored)).toBe(before);
  });

  test.each(NAMES)("%s keeps keys the editor does not model", (name) => {
    const flow = load(name);
    const next = flowReducer(flow, { type: "patchFlow", patch: { global_prompt: "changed" } });
    for (const key of Object.keys(flow)) expect(next).toHaveProperty(key);
    // is_transfer_cf / flex_mode / tool_call_strict_mode are exactly the keys
    // a rebuild-from-typed-model would drop.
    expect(next.is_transfer_cf).toEqual(flow.is_transfer_cf);
  });

  test("the reducer never mutates its input", () => {
    const flow = load("prior_auth_hotline.json");
    const snapshot = JSON.stringify(flow);
    flowReducer(flow, { type: "patchFlow", patch: { global_prompt: "changed" } });
    expect(JSON.stringify(flow)).toBe(snapshot);
  });
});

describe("iterNodeEdges", () => {
  test("yields all five shapes in a stable order", () => {
    const node: FlowNode = {
      id: "n1",
      type: "conversation",
      edges: [{ id: "e1" }, { id: "e2" }],
      else_edge: { id: "else" },
      edge: { id: "single" },
      always_edge: { id: "always" },
      skip_response_edge: { id: "skip" },
    };
    expect(iterNodeEdges(node).map((e) => [e.shape, e.edge.id])).toEqual([
      ["edges", "e1"],
      ["edges", "e2"],
      ["else_edge", "else"],
      ["edge", "single"],
      ["always_edge", "always"],
      ["skip_response_edge", "skip"],
    ]);
  });

  test("matches the worker's shape list", () => {
    expect([...EDGE_SHAPES]).toEqual([
      "edges",
      "else_edge",
      "edge",
      "always_edge",
      "skip_response_edge",
    ]);
  });

  test("the real prior-auth fixture has edges in four of the five shapes", () => {
    const flow = load("prior_auth_hotline.json");
    const shapes = new Set(
      (flow.nodes as FlowNode[]).flatMap((n) => iterNodeEdges(n).map((e) => e.shape)),
    );
    expect(shapes.has("edges")).toBe(true);
    expect(shapes.has("else_edge")).toBe(true);
    expect(shapes.has("edge")).toBe(true);
    expect(shapes.has("skip_response_edge")).toBe(true);
  });
});

describe("reducer actions", () => {
  test("addNode appends a node and mints a unique id", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, {
      type: "addNode",
      nodeType: "end",
      position: { x: 10, y: 20 },
    });
    const nodes = next.nodes as FlowNode[];
    expect(nodes.length).toBe((flow.nodes as FlowNode[]).length + 1);
    const added = nodes[nodes.length - 1];
    expect(added.type).toBe("end");
    expect(added.display_position).toEqual({ x: 10, y: 20 });
    expect(new Set(nodes.map((n) => n.id)).size).toBe(nodes.length);
  });

  test("moveNode writes display_position and nothing else", () => {
    const flow = load("clara_outbound.json");
    const id = (flow.nodes as FlowNode[])[0].id;
    const next = flowReducer(flow, { type: "moveNode", nodeId: id, position: { x: 7, y: 9 } });
    const [before] = flow.nodes as FlowNode[];
    const after = (next.nodes as FlowNode[]).find((n) => n.id === id)!;
    expect(after.display_position).toEqual({ x: 7, y: 9 });
    expect({ ...after, display_position: null }).toEqual({ ...before, display_position: null });
  });

  test("deleteNode also drops every edge pointing at it", () => {
    const flow = load("prior_auth_hotline.json");
    const target = flow.start_node_id as string;
    const next = flowReducer(flow, { type: "deleteNode", nodeId: target });
    const dangling = (next.nodes as FlowNode[]).flatMap((n) =>
      iterNodeEdges(n).filter((e) => e.edge.destination_node_id === target),
    );
    expect(dangling).toEqual([]);
  });

  test("deleting the start node re-points start_node_id at a survivor", () => {
    const flow = load("prior_auth_hotline.json");
    const next = flowReducer(flow, { type: "deleteNode", nodeId: flow.start_node_id as string });
    const ids = (next.nodes as FlowNode[]).map((n) => n.id);
    expect(ids).toContain(next.start_node_id);
  });

  test("connect adds an edges[] entry with a prompt condition", () => {
    const flow = load("clara_outbound.json");
    const [a, b] = flow.nodes as FlowNode[];
    const next = flowReducer(flow, {
      type: "connect",
      nodeId: a.id,
      shape: "edges",
      destinationNodeId: b.id,
    });
    const added = iterNodeEdges((next.nodes as FlowNode[])[0]).at(-1)!;
    expect(added.edge.destination_node_id).toBe(b.id);
    expect(added.edge.transition_condition).toEqual({ type: "prompt", prompt: "" });
  });

  test("a single-edge shape replaces rather than appends", () => {
    const flow = load("clara_outbound.json");
    const [a, b] = flow.nodes as FlowNode[];
    const once = flowReducer(flow, {
      type: "connect",
      nodeId: a.id,
      shape: "else_edge",
      destinationNodeId: b.id,
    });
    const twice = flowReducer(once, {
      type: "connect",
      nodeId: a.id,
      shape: "else_edge",
      destinationNodeId: a.id,
    });
    const node = (twice.nodes as FlowNode[]).find((n) => n.id === a.id)!;
    expect(iterNodeEdges(node).filter((e) => e.shape === "else_edge").length).toBe(1);
  });
});

describe("knownVariables", () => {
  test("collects defaults, extract-node variables and tool response_variables", () => {
    const flow = {
      conversation_flow_id: "f",
      version: 0,
      default_dynamic_variables: { caller_name: "friend" },
      tools: [{ tool_id: "t1", response_variables: { case_id: "$.id" } }],
      nodes: [
        {
          id: "n1",
          type: "extract_dynamic_variables",
          variables: [{ name: "dob", type: "string" }],
        },
      ],
    } as unknown as RawConversationFlow;
    expect(knownVariables(flow).sort()).toEqual(["caller_name", "case_id", "dob"]);
  });
});

describe("seedFlow", () => {
  test("is a runnable two-node graph the worker accepts", () => {
    const seed = seedFlow();
    const nodes = seed.nodes as FlowNode[];
    expect(nodes.map((n) => n.type)).toEqual(["conversation", "end"]);
    expect(seed.start_node_id).toBe(nodes[0].id);
    // The start node reaches the end node, so the graph has no dead end.
    expect(iterNodeEdges(nodes[0])[0].edge.destination_node_id).toBe(nodes[1].id);
  });
});

describe("newNodeId", () => {
  test("is unique across rapid successive calls", () => {
    const ids = Array.from({ length: 50 }, () => newNodeId("conversation"));
    expect(new Set(ids).size).toBe(50);
  });
});
```

- [ ] **Step 3: Run them and watch them fail**

```bash
cd frontend && bun test src/components/flow
```
Expected: FAIL — `Cannot find module '../flowModel'`.

- [ ] **Step 4: Implement `flowModel.ts`**

Write `frontend/src/components/flow/flowModel.ts`. Requirements, in full:

- **Types.** `FlowNode`, `FlowEdge`, `TransitionCondition`, `Equation` — all with a `& Record<string, unknown>` tail. No `interface`; no closed object types for anything that round-trips.
- **`EDGE_SHAPES`** in exactly the order above; it must match `worker/src/arhiteq_worker/flow.py:_SINGLE_EDGE_FIELDS` prefixed by `"edges"`.
- **`NODE_TYPES`** = the supported seven, in palette order: `conversation`, `branch`, `function`, `extract_dynamic_variables`, `transfer_call`, `subagent`, `end`.
- **`iterNodeEdges`** mirrors the worker's function of the same name: `edges[]` first in list order, then the four single-edge fields in `EDGE_SHAPES` order. Skip non-objects. `index` is the position within `edges[]` (`-1` for single-edge shapes) so callers can address an edge for patching.
- **`newNodeId(type)`** returns `` `node-${Date.now()}-${counter++}` `` — Retell's own ids are `node-<ms>`, and the counter is what makes 50 calls in the same millisecond unique (the test above requires it). `newEdgeId(shape)` follows the fixture's `edge-<ms>-<rand>` / `skip-response-edge-<ms>-<rand>` convention.
- **`knownVariables(flow)`** returns a sorted, de-duplicated list from three sources: `default_dynamic_variables` keys, every `extract_dynamic_variables` node's `variables[].name`, and every `tools[].response_variables` key.
- **`seedFlow()`** returns `{ global_prompt, nodes, start_node_id, start_speaker: "agent", model_choice: { type: "cascading", model: "gemini-2.5-flash", high_priority: true } }` with a `conversation` start node (a `static_text` greeting, one `edges[]` entry) wired to an `end` node. It must be a graph the worker loads without raising — no unsupported type, no edge to a missing node.
- **`flowReducer(flow, action)`** — **immutable**. Every action `structuredClone`s the flow, mutates the clone, returns it. Actions:
  - `{type: "patchFlow", patch}` — shallow-merge into the flow root.
  - `{type: "patchNode", nodeId, patch}` — shallow-merge into that node.
  - `{type: "addNode", nodeType, position}` — append `{id, type, name, display_position, ...typeDefaults}`. Type defaults: `conversation`/`subagent` get `instruction: {type: "prompt", text: ""}` and `edges: []`; `branch` gets `edges: []`; `function` gets `edges: []`, `wait_for_result: true`; `end` gets `instruction: {type: "static_text", text: ""}`; `transfer_call` gets `transfer_destination: {type: "predefined", number: ""}`; `extract_dynamic_variables` gets `variables: []`, `edges: []`.
  - `{type: "moveNode", nodeId, position}` — set `display_position` and touch nothing else.
  - `{type: "deleteNode", nodeId}` — remove the node, remove every edge in every shape whose `destination_node_id` is it, and if it was `start_node_id`, re-point at the first remaining node (`""` if none).
  - `{type: "connect", nodeId, shape, destinationNodeId}` — for `"edges"`, append `{id, destination_node_id, transition_condition: {type: "prompt", prompt: ""}}`; for the four single-edge shapes, **replace** any existing one (a node has at most one of each).
  - `{type: "patchEdge", nodeId, shape, index, patch}` — shallow-merge into the addressed edge.
  - `{type: "deleteEdge", nodeId, shape, index}` — remove from `edges[]` by index, or `delete node[shape]` for a single-edge shape.
  - `{type: "setStartNode", nodeId}`.
  - `{type: "addNote" | "patchNote" | "deleteNote", ...}` over `flow.notes`.

Discriminate on `action.type` with a `switch` and make the reducer's return type total, so a missing case is a compile error rather than a silent no-op.

- [ ] **Step 5: Run the tests**

```bash
cd frontend && bun test src/components/flow && bun run lint
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/src/components/flow
git commit -m "feat(flows): the editor's flow document and reducer"
```

---

### Task 3: `flowGraph.ts` — flow JSON ↔ React Flow

**Files:**
- Create: `frontend/src/components/flow/flowGraph.ts`
- Create: `frontend/src/components/flow/__tests__/flowGraph.test.ts`
- Modify: `frontend/package.json` (add `@xyflow/react`)

**Interfaces:**
- Consumes: `flowModel.ts`'s types and `iterNodeEdges`.
- Produces:
  - `function toReactFlow(flow: RawConversationFlow): { nodes: RFNode[]; edges: RFEdge[] }`
  - `type FlowNodeData = { node: FlowNode; isStart: boolean; isGlobal: boolean }`
  - `type FlowEdgeData = { nodeId: string; shape: EdgeShape; index: number; label: string }`
  - `function edgeAddress(rfEdgeId: string): { nodeId: string; shape: EdgeShape; index: number }`

- [ ] **Step 1: Install React Flow**

```bash
cd frontend && bun add @xyflow/react@12.11.2
```
Expected: `package.json` gains the dependency, `bun.lock` updates. It brings `@xyflow/system`, `zustand` and `classcat` transitively — accepted per the spec.

- [ ] **Step 2: Write the failing tests**

`frontend/src/components/flow/__tests__/flowGraph.test.ts`. Reuse the `load`/`FIXTURES` helper shape from `flowModel.test.ts` (duplicating those four lines is fine — a shared test helper module for two files is not worth the indirection).

```typescript
describe("toReactFlow", () => {
  test.each(NAMES)("%s: every node becomes exactly one React Flow node", (name) => {
    const flow = load(name);
    const { nodes } = toReactFlow(flow);
    const graphNodes = nodes.filter((n) => n.type === "flowNode");
    expect(graphNodes.length).toBe((flow.nodes as FlowNode[]).length);
    expect(new Set(graphNodes.map((n) => n.id)).size).toBe(graphNodes.length);
  });

  test("display_position becomes position, with a deterministic fallback", () => {
    const flow = {
      conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [
        { id: "n1", type: "conversation", display_position: { x: 5, y: 6 } },
        { id: "n2", type: "end" },
      ],
    } as unknown as RawConversationFlow;
    const { nodes } = toReactFlow(flow);
    expect(nodes[0].position).toEqual({ x: 5, y: 6 });
    // A node Retell never positioned still has to land somewhere sane and
    // reproducible, or the canvas reshuffles on every render.
    expect(nodes[1].position).toEqual(toReactFlow(flow).nodes[1].position);
  });

  test("the start node and global nodes are flagged for rendering", () => {
    const flow = load("prior_auth_hotline.json");
    const { nodes } = toReactFlow(flow);
    const start = nodes.find((n) => n.id === flow.start_node_id)!;
    expect((start.data as FlowNodeData).isStart).toBe(true);
    // The prior-auth fixture's `branch` node is itself a global node.
    expect(nodes.some((n) => (n.data as FlowNodeData).isGlobal)).toBe(true);
  });

  test("a dangling edge produces no React Flow edge", () => {
    // The real prior-auth fixture has three: two dangling fallbacks and one
    // dangling `edge` on a transfer_call node. React Flow cannot draw an edge
    // with no target, so they are dropped from the canvas (the settings panel
    // still shows them, which is where they get fixed).
    const flow = load("prior_auth_hotline.json");
    const { edges } = toReactFlow(flow);
    const ids = new Set((flow.nodes as FlowNode[]).map((n) => n.id));
    for (const e of edges) expect(ids.has(e.target)).toBe(true);
  });

  test("each edge carries its shape, and the id round-trips through edgeAddress", () => {
    const flow = load("prior_auth_hotline.json");
    const { edges } = toReactFlow(flow);
    for (const e of edges) {
      const data = e.data as FlowEdgeData;
      expect(edgeAddress(e.id)).toEqual({
        nodeId: data.nodeId, shape: data.shape, index: data.index,
      });
    }
  });

  test("two nodes may both carry an edge with the same authored id", () => {
    // `edge-1` appears on several nodes in the real fixtures; React Flow
    // requires globally unique edge ids, so the address must include the node.
    const flow = load("prior_auth_hotline.json");
    const { edges } = toReactFlow(flow);
    expect(new Set(edges.map((e) => e.id)).size).toBe(edges.length);
  });

  test("the edge label is the condition text, or the shape for a runtime edge", () => {
    const node: FlowNode = {
      id: "n1", type: "conversation",
      edges: [{ id: "e1", destination_node_id: "n2",
                transition_condition: { type: "prompt", prompt: "Caller is ready" } }],
      always_edge: { id: "a1", destination_node_id: "n2" },
    };
    const flow = { conversation_flow_id: "f", version: 0, start_node_id: "n1",
                   nodes: [node, { id: "n2", type: "end" }] } as unknown as RawConversationFlow;
    const { edges } = toReactFlow(flow);
    expect((edges[0].data as FlowEdgeData).label).toBe("Caller is ready");
    expect((edges[1].data as FlowEdgeData).label).toBe("always");
  });

  test("an equation condition is labelled readably", () => {
    const cond = { type: "equation", operator: "&&",
                   equations: [{ left: "{{age}}", operator: ">", right: "18" }] };
    const flow = { conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [
        { id: "n1", type: "branch", edges: [
          { id: "e1", destination_node_id: "n2", transition_condition: cond }] },
        { id: "n2", type: "end" },
      ]} as unknown as RawConversationFlow;
    const { edges } = toReactFlow(flow);
    expect((edges[0].data as FlowEdgeData).label).toBe("{{age}} > 18");
  });

  test("notes render as their own node type", () => {
    const flow = load("prior_auth_hotline.json");
    const { nodes } = toReactFlow(flow);
    expect(nodes.filter((n) => n.type === "note").length).toBe(
      (flow.notes as unknown[]).length,
    );
  });
});
```

- [ ] **Step 3: Run and watch fail**

```bash
cd frontend && bun test src/components/flow/__tests__/flowGraph.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `flowGraph.ts`**

- `toReactFlow` walks `flow.nodes`, emitting `{ id, type: "flowNode", position, data: { node, isStart, isGlobal } }`. `isGlobal` is `Boolean(node.global_node_setting?.condition)` — the same test the worker's `FlowGraph.global_nodes` uses. Position comes from `display_position`, falling back to a **deterministic** grid derived from the node's index (`{x: 40 + (i % 4) * 320, y: 40 + Math.floor(i / 4) * 220}`), never `Math.random()`.
- `flow.notes` become `{ id, type: "note", position, data: { note }, style: { width, height } }` from the note's own `display_position` and `size`.
- Edges: for every node, `iterNodeEdges(node)` → one React Flow edge per entry **whose `destination_node_id` names an existing node**. Id is `` `${nodeId}::${shape}::${index}` `` — authored edge ids repeat across nodes in the real fixtures, and React Flow requires global uniqueness. `edgeAddress` parses that back (split on `"::"`, `index` via `Number`).
- `data.label`: a `prompt` condition's `prompt`; an `equation` condition rendered as `left op right` joined by the combinator (` && ` / ` || `); otherwise the shape's own word (`"always"`, `"skip response"`, `"else"`, `"failed"`).
- Edge styling by shape, so the graph does not collapse into one connector look: `edges` solid; `else_edge` dashed + muted; `edge` dashed + `bad` colour; `always_edge` solid + arrow marker; `skip_response_edge` dotted. Express it as `{ style, animated, markerEnd }` on the React Flow edge — no CSS file.

- [ ] **Step 5: Run and commit**

```bash
cd frontend && bun test src/components/flow && bun run lint && bun run build
git add frontend/package.json frontend/bun.lock frontend/src/components/flow
git commit -m "feat(flows): adapt a conversation flow onto a React Flow graph"
```

---

### Task 4: The canvas

**Files:**
- Create: `frontend/src/components/flow/FlowEditor.tsx`
- Create: `frontend/src/components/flow/NodePalette.tsx`
- Create: `frontend/src/components/flow/nodes/nodeMeta.ts`
- Create: `frontend/src/components/flow/nodes/NodeShell.tsx`
- Create: `frontend/src/components/flow/nodes/FlowNode.tsx`
- Create: `frontend/src/components/flow/nodes/NoteNode.tsx`

**Interfaces:**
- Consumes: `toReactFlow`, `edgeAddress`, `FlowNodeData`, `FlowEdgeData` (Task 3); `flowReducer`, `FlowAction`, `NODE_TYPES` (Task 2).
- Produces:
  ```typescript
  export default function FlowEditor(props: {
    flow: RawConversationFlow;
    dispatch: (action: FlowAction) => void;
    readOnly: boolean;
  }): JSX.Element;
  ```
  `FlowEditor` owns *only* selection state; every mutation goes out through `dispatch`. Task 8 supplies `flow` and `dispatch` from the page.

**Note on structure:** the spec says "one component per type over a shared `NodeShell`". Seven components that differ only in icon, accent and subtitle is duplication, not clarity — so this plan uses `nodeMeta.ts` (a per-type record) plus one `FlowNode.tsx` reading it. The spec's intent, that each type *reads* distinctly on the canvas, is fully met. The per-type split that does earn its keep is the **settings** editors (Task 6), where the field sets genuinely differ.

- [ ] **Step 1: `nodeMeta.ts`**

```typescript
import {
  ArrowRightLeft, GitBranch, MessagesSquare, PhoneForwarded,
  Braces, Bot, CircleStop, type LucideIcon,
} from "lucide-react";
import type { FlowNode } from "../flowModel";

export type NodeMeta = {
  label: string;
  icon: LucideIcon;
  /** Tailwind classes for the node's title bar. */
  accent: string;
  /** One line of the node's own content, for the canvas card. */
  subtitle: (node: FlowNode) => string;
};

const instructionText = (node: FlowNode): string => {
  const i = node.instruction as { text?: string } | undefined;
  return typeof i?.text === "string" ? i.text : "";
};

export const NODE_META: Record<string, NodeMeta> = {
  conversation: { label: "Conversation", icon: MessagesSquare,
    accent: "bg-accent-soft text-accent-deep", subtitle: instructionText },
  subagent: { label: "Subagent", icon: Bot,
    accent: "bg-accent-soft text-accent-deep", subtitle: instructionText },
  branch: { label: "Branch", icon: GitBranch,
    accent: "bg-amber-50 text-amber-900", subtitle: () => "Routes without speaking" },
  function: { label: "Function", icon: ArrowRightLeft,
    accent: "bg-violet-50 text-violet-900",
    subtitle: (n) => (typeof n.tool_id === "string" ? n.tool_id : "No tool selected") },
  extract_dynamic_variables: { label: "Extract variables", icon: Braces,
    accent: "bg-violet-50 text-violet-900",
    subtitle: (n) => ((n.variables as { name?: string }[]) ?? [])
      .map((v) => v.name).filter(Boolean).join(", ") || "No variables" },
  transfer_call: { label: "Transfer", icon: PhoneForwarded,
    accent: "bg-sky-50 text-sky-900",
    subtitle: (n) => {
      const d = n.transfer_destination as { number?: string; prompt?: string } | undefined;
      return d?.number || d?.prompt || "No destination";
    } },
  end: { label: "End call", icon: CircleStop,
    accent: "bg-neutral-100 text-neutral-700", subtitle: instructionText },
};

/** A node type the graph carries but this editor does not model (never from
 *  our own palette — only from a flow imported with a newer node type). */
export const UNKNOWN_META: NodeMeta = {
  label: "Unsupported node", icon: CircleStop,
  accent: "bg-rose-50 text-rose-900",
  subtitle: (n) => `type: ${String(n.type)}`,
};
```

Check every icon name against `lucide-react` before committing — a wrong name is a build failure. Confirm the `accent-soft` / `accent-deep` colour tokens exist in the Tailwind config; if not, use the nearest token the codebase already uses.

- [ ] **Step 2: `NodeShell.tsx`**

A presentational shell: `Handle` (target, `Position.Top`) · title bar (icon, `node.name` or the meta label, a "START" pill when `isStart`, a globe pill when `isGlobal`) · subtitle line, clamped to two lines · `Handle` (source, `Position.Bottom`). Selected state gets `ring-1 ring-ink`. Fixed width `w-[260px]`. Import `Handle` and `Position` from `@xyflow/react`.

- [ ] **Step 3: `FlowNode.tsx` and `NoteNode.tsx`**

`FlowNode` reads `NODE_META[data.node.type] ?? UNKNOWN_META` and renders `NodeShell`. Wrap the default export in `memo` — React Flow re-renders every node on every store change, and the canvas visibly stutters at 18 nodes without it. `NoteNode` renders `note.content` on an amber sticky with `NodeResizer` if you want resizing; if not, plain and non-interactive is acceptable for v1 — just keep positions round-tripping.

- [ ] **Step 4: `NodePalette.tsx`**

A left rail listing `NODE_TYPES` with their meta icon/label. Clicking one dispatches `addNode` at a position near the viewport centre. Also make each item `draggable` and set `event.dataTransfer.setData("application/arhiteq-node", type)`, which `FlowEditor`'s `onDrop` reads — the palette does not need to know about the canvas transform. Disabled entirely when `readOnly`.

- [ ] **Step 5: `FlowEditor.tsx`**

```typescript
"use client";

import "@xyflow/react/dist/style.css";
```

Read `node_modules/next/dist/docs/` on importing CSS from a package inside a client component before writing this — if this Next version routes it differently, follow the docs, not this snippet.

The component:
- `const { nodes, edges } = useMemo(() => toReactFlow(flow), [flow])` — the flow object is the single source of truth; React Flow's arrays are always derived, never separately owned state. This is what keeps the fidelity rule true at the UI layer.
- `onNodesChange`: apply `position` changes by dispatching `moveNode` on `dragging === false` only (dispatching per animation frame would fire the 800 ms autosave on every pixel). Apply `select` changes to local selection state. Apply `remove` by dispatching `deleteNode`.
- `onEdgesChange`: `remove` → dispatch `deleteEdge` via `edgeAddress(id)`.
- `onConnect({source, target})` → dispatch `connect` with `shape: "edges"`.
- `onDrop` → read the dataTransfer type, convert the drop point with `screenToFlowPosition` from `useReactFlow`, dispatch `addNode`.
- Render `<Background />`, `<Controls />`, `<MiniMap />`.
- `nodeTypes` and `edgeTypes` must be **module-level constants**, not inline object literals — a new object identity each render makes React Flow remount every node and log a warning.
- Wrap in `<ReactFlowProvider>` so `useReactFlow` works, and give the container an explicit height (`h-full`); React Flow renders nothing in a zero-height parent.
- `readOnly` sets `nodesDraggable={false} nodesConnectable={false} elementsSelectable` still true (viewing a published version must still let you inspect a node).
- Three panes: `<NodePalette>` (`w-[180px] shrink-0`) · canvas (`flex-1`) · the settings pane (`w-[320px] shrink-0`). In this task the settings pane is a placeholder `<div>`; Task 6 replaces it with `<NodeSettings>`. **`FlowEditor`'s public props stay exactly `{flow, dispatch, readOnly}`** — the settings pane is internal, not a `children` slot, because it needs the selection state `FlowEditor` owns and the page has no reason to know about it.

Selection: hold `selectedNodeId: string | null` in `FlowEditor` and pass it (plus a setter) straight down to the settings pane as props. No context, no render prop — one component deep does not need either. The mutation path stays one-way through `dispatch`.

- [ ] **Step 6: Build**

```bash
cd frontend && bun run lint && bun run build && bun test src/components/flow
```
Expected: PASS. (There are no rendering tests — `bun test` covers the pure modules; the canvas is verified by the build and by Task 10's manual pass.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/flow
git commit -m "feat(flows): the flow editor canvas"
```

---

### Task 5: The condition editor

The one piece of genuinely deterministic logic in the graph, and the piece a user will get wrong most often.

**Files:**
- Create: `frontend/src/components/flow/settings/ConditionEditor.tsx`
- Create: `frontend/src/components/flow/settings/EquationBuilder.tsx`
- Modify: `frontend/src/components/flow/__tests__/flowModel.test.ts` (add the two helpers below)
- Modify: `frontend/src/components/flow/flowModel.ts`

**Interfaces:**
- Produces:
  - `flowModel.ts`: `const EQUATION_OPERATORS: readonly string[]`, `function emptyCondition(type: "prompt" | "equation"): TransitionCondition`
  - `ConditionEditor(props: { condition, onChange, variables: string[] })`
  - `EquationBuilder(props: { condition, onChange, variables: string[] })`

- [ ] **Step 1: Write the failing tests**

Add to `flowModel.test.ts`:

```typescript
describe("conditions", () => {
  test("the operator list matches the worker's", () => {
    // worker/src/arhiteq_worker/flow.py: _NUMERIC_OPERATORS, _EQUALITY_OPERATORS,
    // _CONTAINMENT_OPERATORS, plus the unary `exists`. Offering an operator the
    // worker does not implement produces an edge that silently never fires.
    expect([...EQUATION_OPERATORS]).toEqual([
      "==", "!=", ">", "<", "CONTAINS", "NOT CONTAINS", "exists",
    ]);
  });

  test("switching condition type produces the shape the worker parses", () => {
    expect(emptyCondition("prompt")).toEqual({ type: "prompt", prompt: "" });
    expect(emptyCondition("equation")).toEqual({
      type: "equation",
      operator: "&&",
      equations: [{ left: "", operator: "==", right: "" }],
    });
  });

  test("an equation condition with no equations is rejected by the worker", () => {
    // `evaluate_equation_condition` returns False for an empty `equations`
    // list, so the editor must never produce one -- hence the seeded row above.
    expect((emptyCondition("equation").equations as unknown[]).length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run, fail, implement the two `flowModel` exports**

```bash
cd frontend && bun test src/components/flow -t conditions
```
Expected: FAIL, then PASS after adding `EQUATION_OPERATORS` and `emptyCondition`.

- [ ] **Step 3: `EquationBuilder.tsx`**

One row per equation: `left` (a combobox — a `<Select>` of `variables` rendered as `{{name}}`, plus a free-text `<TextInput>` so a literal or an unlisted variable is still typeable), `operator` (`<Select>` over `EQUATION_OPERATORS`), `right` (`<TextInput>`, **hidden when the operator is `exists`** — it is unary and the worker ignores the right side). A remove button per row, an "Add condition" button, and a two-state `&&` / `||` toggle shown only when there are two or more rows.

Show a one-line hint under the rows explaining evaluation order, because it is not guessable and it is what the worker actually does:

> Equations are checked in order before the model is asked anything. The first edge whose condition is true wins.

- [ ] **Step 4: `ConditionEditor.tsx`**

A two-way segmented control (`Prompt` / `Equation`) over `condition.type`, calling `onChange(emptyCondition(next))` when it flips — **and warn before discarding**: switching away from a non-empty condition throws away authored text, so confirm with a `Modal` or require a second click. Below it, either a `<Textarea>` bound to `condition.prompt` or the `EquationBuilder`.

- [ ] **Step 5: Build and commit**

```bash
cd frontend && bun test src/components/flow && bun run lint && bun run build
git add frontend/src/components/flow
git commit -m "feat(flows): prompt and equation transition condition editors"
```

---

### Task 6: Node settings

**Files:**
- Create: `frontend/src/components/flow/settings/NodeSettings.tsx`
- Create: `frontend/src/components/flow/settings/EdgeList.tsx`
- Create: `frontend/src/components/flow/settings/ConversationSettings.tsx`
- Create: `frontend/src/components/flow/settings/BranchSettings.tsx`
- Create: `frontend/src/components/flow/settings/FunctionSettings.tsx`
- Create: `frontend/src/components/flow/settings/TransferSettings.tsx`
- Create: `frontend/src/components/flow/settings/EndSettings.tsx`
- Create: `frontend/src/components/flow/settings/ExtractSettings.tsx`
- Modify: `frontend/src/components/flow/FlowEditor.tsx` (mount `NodeSettings` in the settings slot)

**Interfaces:**
- Every per-type editor takes the same props, so `NodeSettings` can dispatch on `node.type` with a lookup rather than a switch of differing signatures:
  ```typescript
  export type NodeSettingsProps = {
    node: FlowNode;
    flow: RawConversationFlow;
    dispatch: (action: FlowAction) => void;
    variables: string[];
  };
  ```

- [ ] **Step 1: `EdgeList.tsx`**

Every editor needs it, so build it first. Groups `iterNodeEdges(node)` by shape with a heading per shape and a one-line explanation of what that shape *does at runtime* — this is the single highest-value piece of copy in the editor, because the semantics are not guessable:

| Shape | Heading | Explanation |
|---|---|---|
| `edges` | Transitions | Offered to the model, or evaluated as equations. |
| `else_edge` | Fallback | Taken when nothing else matches. |
| `edge` | On failure | Taken when the transfer cannot be completed. |
| `always_edge` | Always | Taken on the caller's next turn, unconditionally. |
| `skip_response_edge` | Say and continue | The node speaks its line, then moves on **without waiting for the caller**. |

Each row: destination `<Select>` over the flow's other node ids (labelled by node name), the condition summary, an expand-to-`ConditionEditor` control, and a delete button. **A dangling edge (no destination) must be flagged**: the real prior-auth fixture has three, and the worker treats a dangling fallback on a routing node as a dead end that ends the call. Render it with a `bad`-toned warning saying so.

`always_edge` and `skip_response_edge` do not take a model-facing condition (the worker never offers them to the model), so hide the `ConditionEditor` for those two shapes and show the destination only.

- [ ] **Step 2: The per-type editors**

`EndSettings.tsx` is the shortest, so write it first as the pattern every sibling follows — same props, same `Field` usage, same `dispatch` shape, no local state:

```tsx
"use client";

import Toggle from "@/components/ui/Toggle";
import { Field, Textarea } from "@/components/ui/Field";
import type { NodeSettingsProps } from "./NodeSettings";

export default function EndSettings({ node, dispatch }: NodeSettingsProps) {
  const instruction = (node.instruction ?? {}) as { type?: string; text?: string };
  const speaks = Boolean(node.speak_during_execution);

  return (
    <>
      <Field
        label="Closing line"
        hint="Only spoken when “Say a closing line” is on — otherwise the call just ends."
      >
        <Textarea
          rows={3}
          value={instruction.text ?? ""}
          onChange={(e) =>
            dispatch({
              type: "patchNode",
              nodeId: node.id,
              // Preserve the instruction's own type: a `prompt` line is
              // phrased by the model, a `static_text` one is spoken verbatim,
              // and silently flipping it changes what the caller hears.
              patch: { instruction: { ...instruction, text: e.target.value } },
            })
          }
        />
      </Field>
      <Field label="Say a closing line" className="mt-3">
        <Toggle
          checked={speaks}
          onChange={(v) =>
            dispatch({ type: "patchNode", nodeId: node.id, patch: { speak_during_execution: v } })
          }
        />
      </Field>
    </>
  );
}
```

Note the two rules the skeleton encodes and every sibling must keep: **read through `??` and cast at the edge** (node fields are `unknown` by design), and **spread the existing sub-object** when patching one of its keys, never replace it — replacing is how a key the editor does not model gets dropped from a node.

Common to all editors, rendered by `NodeSettings` above the per-type body so each one does not repeat it: `name` (`TextInput`), the node id (`CopyId`), `EdgeList`, and a "Set as start node" button when it is not already the start. Then, per type:

- **`ConversationSettings`** (used for `conversation` *and* `subagent`): `instruction.type` segmented control (`Prompt` / `Exact text`) + `Textarea` for `instruction.text`; per-node `start_speaker` (`Select`: inherit / agent / user); `global_node_setting.condition` (`Textarea`, with a note that a global node is reachable from **anywhere** in the flow). Label the two instruction types honestly: **Prompt** = "the model phrases it"; **Exact text** = "spoken verbatim".
- **`BranchSettings`**: `instruction.text` as the routing question, `EdgeList`, and a note that a branch node speaks nothing and costs one extra model call unless every edge is an equation.
- **`FunctionSettings`**: `tool_id` (`Select` over `flow.tools` by `name`, showing `tool_id` as the value), `wait_for_result` (`Toggle`), `speak_during_execution` (`Toggle`). Warn when `tool_id` resolves to nothing — the worker skips the tool with a warning and the node then cannot act.
- **`TransferSettings`**: `transfer_destination.type` (`predefined` / `inferred`), then `number` or `prompt`. **Validate `number` as E.164 client-side and say so plainly** — `worker/.../tools.py:E164_RE` (`^\+[1-9]\d{1,14}$`) is enforced unconditionally at runtime with no opt-out, so a non-E.164 destination silently takes the failure edge. Do **not** surface `ignore_e164_validation` as a control: the worker deliberately ignores it (`docs/SECURITY.md` § Transfer destinations), so a toggle here would be a lie. Also `speak_during_execution` (`Toggle`).
- **`EndSettings`**: `instruction` (same two-way control as conversation) and `speak_during_execution`, with a note that the closing line is only spoken when that is on.
- **`ExtractSettings`**: a row-per-variable editor over `node.variables` — `name`, `type` (`string` / `number` / `boolean` / `enum`), `description`, and `choices` when `enum`. The shape must match `worker/.../tools.py:extract_variable_parameters`, which reads `{name, type, description, choices?, examples?}`.

- [ ] **Step 3: `NodeSettings.tsx`**

Header (meta icon + label), then the editor from a `Record<string, ComponentType<NodeSettingsProps>>`, falling back to a read-only JSON view for an unmodelled type — an imported flow may carry one, and showing nothing would look like a bug. Empty state when no node is selected: point at the palette.

- [ ] **Step 4: Mount it in `FlowEditor` and build**

```bash
cd frontend && bun run lint && bun run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/flow
git commit -m "feat(flows): per-node settings and the edge list"
```

---

### Task 7: Global settings, and the rail edits the flow

The right rail currently prints "Not available for conversation-flow agents." in three places. A flow has all three concepts — `tools`, `knowledge_base_ids` + `kb_config`, `mcps` — so those sections now edit the flow. Genuinely flow-only settings (`global_prompt`, `model_choice`, `model_temperature`, `start_speaker`) go in the editor's own Global Settings pane.

**Files:**
- Create: `frontend/src/components/flow/settings/GlobalSettings.tsx`
- Modify: `frontend/src/components/editor/sections/FunctionsSection.tsx`
- Modify: `frontend/src/components/editor/sections/KnowledgeBaseSection.tsx`
- Modify: `frontend/src/components/editor/sections/McpSection.tsx`

- [ ] **Step 1: Check the shapes actually match before changing anything**

An LLM's `general_tools[]` and a flow's `tools[]` are both Retell tool entries, but confirm it rather than assume: compare `backend/tests/fixtures/retell_flows/prior_auth_hotline.json`'s `tools[0]` keys against what `FunctionsSection` reads. The fixture's keys are `args_at_root, description, headers, method, name, parameter_type, parameters, query_params, response_variables, speak_after_execution, speak_during_execution, timeout_ms, tool_id, type, url`.

**If they differ materially, stop and report it rather than coercing** — a lossy adapter between two tool shapes is exactly the silent-drop bug this whole feature exists to avoid. In that case the fallback is a flow-specific tools editor in `GlobalSettings`, and you should say so in your report.

- [ ] **Step 2: Widen the three sections**

Each already takes a value and an `onChange`. Keep those signatures and let the page pass either source — no `agentType` prop, no branching inside the section. `KnowledgeBaseSection` additionally needs the flow's `kb_config` (`{top_k, filter_score}`); add it as an **optional** prop so the LLM path is untouched.

- [ ] **Step 3: `GlobalSettings.tsx`**

A pane in the editor's right rail, above `NodeSettings` or on a tab beside it: `global_prompt` (`Textarea`, prepended to **every** node's instructions — say that in the help text), `start_speaker` (`Select`: agent / user, with "who speaks first when the call connects"), `model_choice.model` (reuse `LlmModelSelect`; note that a flow imported from Retell may name an OpenAI model and that Arhiteq maps it onto Gemini), and `model_temperature` (`Slider`).

- [ ] **Step 4: Build and commit**

```bash
cd frontend && bun run lint && bun run build
git add frontend/src/components/flow frontend/src/components/editor/sections
git commit -m "feat(flows): global flow settings, and the rail edits a flow's own tools"
```

---

### Task 8: Wire the editor into `/agents/[id]`

**Files:**
- Modify: `frontend/src/app/agents/[id]/page.tsx`

This is the integration task and the one most likely to break the single-prompt path. **That path is the entire current product — treat any change to it as a defect unless the diff makes it unavoidable.**

- [ ] **Step 1: Load the flow**

Add `const [flow, setFlow] = useState<RawConversationFlow | null>(null)` and set it from `detail.flow` in the existing `getAgentDetail` effect.

- [ ] **Step 2: Draft and autosave**

Add `flowDraft: Partial<RawConversationFlow>` next to `agentDraft` / `llmDraft`, mirrored into a ref like the others. In `flush`, after the LLM block:

```typescript
      if (flowId && Object.keys(pendingFlow).length) {
        setFlow(await api.updateConversationFlow(flowId, pendingFlow));
        setFlowDraft((prev) => omitSent(prev, pendingFlow));
        // A flow edit forks the agent's draft server-side (update-conversation-flow
        // seeds and touches every agent using the flow), so the agent's version
        // moved even though we did not PATCH it -- exactly like the LLM branch
        // above. Without this refresh the editor still believes it is on the
        // published version and locks itself read-only mid-edit.
        if (!isChat && !Object.keys(pendingAgent).length) {
          saved = await api.getAgent(id);
          setAgent(saved);
        }
      }
```

Include `flowDraft` in `dirty`, in the debounce effect's deps, and in the `beforeunload` guard. Clear it everywhere `llmDraft` is cleared (`selectVersion`, `reload`, `handleDiscard`).

- [ ] **Step 3: The dispatch bridge**

`FlowEditor` takes `dispatch(action)`; the page holds drafts. Bridge them so a reducer action becomes a draft patch:

```typescript
  // The flow the editor edits is the server value overlaid with unsaved edits,
  // exactly like `view` / `llmView` above.
  const flowView: RawConversationFlow | null = flow ? { ...flow, ...flowDraft } : null;

  const dispatchFlow = useCallback(
    (action: FlowAction) => {
      setFlowDraft((prev) => {
        const current = { ...flow, ...prev } as RawConversationFlow;
        const next = flowReducer(current, action);
        // Send only what actually changed: PATCHing all nineteen mutable
        // fields on every keystroke would make each edit look like a full
        // rewrite in the version history.
        const patch: Partial<RawConversationFlow> = {};
        for (const key of Object.keys(next)) {
          if (!Object.is((next as Record<string, unknown>)[key],
                         (current as Record<string, unknown>)[key])) {
            (patch as Record<string, unknown>)[key] = (next as Record<string, unknown>)[key];
          }
        }
        return { ...prev, ...patch };
      });
    },
    [flow],
  );
```

`flow` must be in the deps, and the setter must read `flow` fresh — a stale closure here silently drops edits made during an in-flight save.

- [ ] **Step 4: Branch the layout**

In the Create tab, replace the left `fieldset` when `flowView` is non-null:

```tsx
{flowView ? (
  <FlowEditor flow={flowView} dispatch={dispatchFlow} readOnly={readOnly} />
) : (
  <fieldset disabled={readOnly} className="...">{/* unchanged prompt column */}</fieldset>
)}
```

Delete the three "Not available for conversation-flow agents." placeholders and the "prompt editing is not available yet" paragraph, passing the flow's values into the rail sections instead (Task 7). Keep `SelectorRow` for the agent-level voice/language/timezone; its `model` / `temperature` come from the flow now — or hide those two controls for flow agents and leave them to `GlobalSettings`. **Pick one and do not render the model in both places.**

- [ ] **Step 5: Pinned versions render the frozen graph**

In `selectVersion`, the older-version branch sets `setLlm(pinned.response_engine_config)`. Add `setFlow(pinned.conversation_flow ?? null)` beside it — Task 1 made that field real. Without this, viewing V3 of a flow agent shows today's graph under a "Viewing V3 — published versions are immutable" banner, which is a lie the whole feature exists to prevent.

- [ ] **Step 6: Build and commit**

```bash
cd frontend && bun run lint && bun run build
git add frontend/src/app/agents/\[id\]/page.tsx
git commit -m "feat(flows): edit a conversation flow from the agent editor"
```

---

### Task 9: Creating one, and testing one

The card is the feature flag — enabling it is what makes the whole feature reachable, so it lands last (spec § Rollout: "there is never a window in which the dashboard can mint an agent that cannot take a call").

**Files:**
- Modify: `frontend/src/components/agents/CreateAgentModal.tsx`
- Modify: `frontend/src/components/simulation/SimulationTab.tsx`

- [ ] **Step 1: Enable the card**

In `CreateAgentModal.tsx` (around line 166) remove `disabled={t.key === "flow"}`, the `title` tooltip, the `opacity-50` branch and the "Coming soon" pill, and make `onClick` set the type for both keys.

- [ ] **Step 2: Create the flow, then the agent**

`handleCreate` (around line 105) currently does `api.createLlm(...)` then `api.createAgent(...)`. Branch the engine half only, leaving the `try` / `catch` / `onClose` / `router.push` structure exactly as it is:

```typescript
  const handleCreate = async () => {
    const tpl = TEMPLATES.find((t) => t.name === template);
    setCreating(true);
    setError(null);
    try {
      // Two steps either way: the engine is created first, then the agent that
      // points at it. A flow agent's seed graph already reaches an end node, so
      // it is callable the moment it exists.
      const response_engine =
        type === "flow"
          ? {
              type: "conversation-flow" as const,
              conversation_flow_id: (await api.createConversationFlow(seedFlow()))
                .conversation_flow_id,
            }
          : {
              type: "retell-llm" as const,
              llm_id: (
                await api.createLlm({
                  ...(tpl?.prompt ? { general_prompt: tpl.prompt } : {}),
                  ...(tpl?.beginMessage ? { begin_message: tpl.beginMessage } : {}),
                })
              ).llm_id,
            };
      const agent = await api.createAgent({
        agent_name: template === "Build from scratch" ? "New Agent" : template,
        response_engine,
        voice_id: "cartesia-sonic-english",
      });
      onClose();
      router.push(`/agents/${agent.agent_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create agent");
      setCreating(false);
    }
  };
```

`seedFlow()` is Task 2's; import it from `@/components/flow/flowModel`. The template list is prompt-only — a template picked alongside the flow card contributes its **name** and nothing else, which is correct and needs no extra handling.

- [ ] **Step 3: Let a flow agent be tested**

`SimulationTab.tsx` returns early on `if (!llm)` (around line 279), which blocks the **whole** tab — including `TestPanel`, the web-call surface, which needs only `agentId` and `agentVersion`. There is currently no way to place a test call to a flow agent from the dashboard.

Replace the early return with a narrower gate: when `llm` is null, render `TestPanel` in the same slot, plus one line explaining that saved test cases and batch runs need a prompt-based agent. Everything that dereferences `llmId` must stay behind the existing guard. Keep the copy accurate — flow support in the simulation *suite* is a deliberate follow-up (`services/simulation.py` builds a `retell-llm` engine), not a missing feature here.

- [ ] **Step 4: Build and commit**

```bash
cd frontend && bun run lint && bun run build
git add frontend/src/components/agents/CreateAgentModal.tsx frontend/src/components/simulation/SimulationTab.tsx
git commit -m "feat(flows): create a conversation flow agent and place a test call to it"
```

---

### Task 10: Verify end to end, and write it down

- [ ] **Step 1: Full local run**

```bash
docker compose up -d
make api    # separate terminal
make worker # separate terminal
make web    # separate terminal
```

Then, in the dashboard: create a Conversational flow agent · confirm the seed graph renders · add a `branch` node, wire an edge, give it a prompt condition · add an equation condition on another edge · watch the save chip go pending → saved · reload and confirm everything persisted · **Publish**, then edit the graph again and check the version panel shows a new draft · select the published version and confirm the canvas shows the **pre-edit** graph and is read-only · then Test Audio and place a real web call through the graph.

The call is the part that matters most: plan 2 shipped with a **stated live-evidence gap** — no caller-speech-driven `transition_to` → `advance` → `update_instructions` round trip has ever run against a real `AgentSession`. This is the first opportunity to close it. **Speak into the call and drive at least one edge transition**, then check the worker log for a `flow transition call=… X -> Y via edge …` line. If it does not appear, that is a plan-2 runtime finding, not an editor bug — report it with the log rather than working around it.

- [ ] **Step 2: Stop everything you started**

```bash
docker compose down
```
Kill the api/worker/web processes. Do not leave background servers or containers running.

- [ ] **Step 3: Docs**

- `docs/UI_INVENTORY.md` — the editor's three panes and what each pane owns.
- `docs/API_COVERAGE.md` — mark the conversation-flow endpoints as dashboard-reachable.
- `docs/ARCHITECTURE.md` — one paragraph in the conversation-flow section: the editor round-trips opaque JSON, and the fidelity test is what enforces it.
- Record the two things a reader will otherwise trip on: the palette offers exactly the seven types the worker executes, and `ignore_e164_validation` is intentionally absent from the transfer editor.

- [ ] **Step 4: Full sweep and commit**

```bash
cd frontend && bun test && bun run lint && bun run build
cd ../backend && uv run pytest -q
cd ../worker && uv run --only-group dev pytest -q
cd .. && pre-commit run --all-files   # the two backend/scripts EXE001 failures are pre-existing
```

```bash
git add docs
git commit -m "docs(flows): the conversation flow editor"
```

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/conversation-flow-editor
gh pr create --title "feat(flows): conversation flow editor" --body "..."
```

The body must state: what the editor can and cannot do; that the fidelity guard is a real test over the three real fixtures, not a convention; the result of the live call in Step 1 including whether an edge transition was observed; and any follow-up found along the way.

---

## Risks and open questions

- **The fidelity rule is the whole feature.** Every task that touches flow JSON must go through `flowReducer`. A component that does `setFlow({...flow, nodes: rebuiltNodes})` defeats the guard without failing a test, because the test only exercises the reducer. Reviewers: look for object construction, not just for test coverage.
- **Autosave granularity.** `dispatchFlow` diffs at the top level, so any node edit sends the whole `nodes` array. That is correct but chatty; `update-conversation-flow` bumps the flow version on every PATCH. If the version history becomes unreadable, the fix is debouncing harder, not diffing deeper — do not attempt per-node PATCH semantics, the endpoint does not support it.
- **React Flow's controlled mode.** `nodes` and `edges` are derived from `flow` on every render. If dragging feels laggy at 18 nodes, memoize per node rather than introducing a second copy of the graph in local state — a second copy is how the two drift.
- **`@xyflow/react` in a Next 16 client component.** Untested in this repo. If the CSS import or SSR causes trouble, check `node_modules/next/dist/docs/` first; a `dynamic(..., { ssr: false })` wrapper is the fallback, not the opening move.
- **`extract_dynamic_variables` has no fixture.** No real captured flow contains one, so its editor is built against the worker's `extract_variable_parameters` reader rather than observed Retell output. Note it in the PR; the shape may need correcting when a real one turns up.
