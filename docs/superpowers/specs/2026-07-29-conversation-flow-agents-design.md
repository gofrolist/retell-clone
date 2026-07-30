# Conversation flow agents — design

**Date:** 2026-07-29
**Status:** approved, pending implementation plan
**Scope:** authoring (dashboard editor) + execution (voice worker) for
`response_engine.type == "conversation-flow"` agents.

## Goal

Retell offers two voice agent types: **single prompt** and **conversational
flow**. Arhiteq ships only the first. A flow agent is a directed graph of nodes
— prompts, tool calls, branches, transfers — that the runtime walks during a
call, giving deterministic control that a single prompt cannot.

This spec covers the flow feature end to end. Three sibling gaps are explicitly
*out of scope* and get their own specs: native chat-agent creation, the template
catalogue, and flow support in the Simulation suite.

## Where we start from

Already built:

- `ConversationFlow` model and full CRUD (`api/conversation_flows.py`) —
  create / get / list / update / delete, keyset-paginated, version counter.
- `response_engine.conversation_flow_id` exists in the agent schema
  (`schemas.py:107`) but nothing reads it.
- The Retell migration script already copies flows verbatim
  (`scripts/migrate_retell_agent.py`).

Missing:

- Any flow editor. `frontend/package.json` has no graph library, and the
  "Conversational flow" card in the create modal is hard-disabled
  (`CreateAgentModal.tsx:166`).
- Any flow execution. The worker is single-prompt only — `main.py:807` builds
  instructions from `cfg.llm.general_prompt` and nothing else.
- Fidelity: our request schema accepts 7 fields; real flows carry 19. See
  [Fidelity gap](#the-fidelity-gap).

## Schema authority: real Retell fixtures

The per-node field names are not reconstructed from documentation or memory.
They are pinned by three flows pulled from the live Retell account on
2026-07-29 and committed, sanitized, to `backend/tests/fixtures/retell_flows/`:

| Fixture | Nodes | Covers |
|---|---|---|
| `prior_auth_hotline.json` | 18 | every node type incl. `subagent`, `branch`, `components`, `notes`, flow-level `tools` |
| `clara_outbound.json` | 7 | six custom HTTP tools, `response_variables`, two components |
| `identity_verify_transfer.json` | 16 | transfer-heavy graph, `transfer_destination` / `transfer_option` |

Sanitization replaced one Supabase project host with `example-tools.invalid`
and two real phone numbers with `+1555555010x`. No credentials were present —
every `headers` and `query_params` object in all eight account flows was empty.

These fixtures are the shared source of truth for backend, worker and frontend
tests. Refreshing them is a manual step against Retell's
`GET /v2/list-conversation-flows`.

**Fixtures pin what exists; Retell's OpenAPI spec pins what is possible.** The
account's flows exercise only a subset of the schema — they contain no
`equation` transition condition, no `extract_dynamic_variables` node and no
`component` node, though all three are documented. Where the two sources
disagree, the fixtures win for *shape* (they are what the live API actually
returns) and the docs win for *coverage*. One known disagreement: the docs name
a flag `is_transfer_llm`, the live API returns `is_transfer_cf`. Accept and
store both.

### Flow object

Nineteen top-level keys observed across the account:

| Key | Notes |
|---|---|
| `conversation_flow_id`, `version`, `last_modification_timestamp` | server-managed |
| `nodes` | the graph (below) |
| `start_node_id`, `start_speaker` | entry point; `start_speaker` is `agent`/`user` |
| `global_prompt` | prepended to every node's instructions |
| `tools` | flow-scoped tool definitions, referenced by `function` nodes via `tool_id` |
| `components` | subflows — see below |
| `notes` | canvas annotations: `{id, content, size, display_position}` |
| `model_choice` | e.g. `{"type": "cascading", "model": "gpt-5.1", "high_priority": true}` |
| `model_temperature`, `tool_call_strict_mode` | model knobs |
| `knowledge_base_ids`, `kb_config` | `kb_config` is `{top_k, filter_score}` |
| `begin_tag_display_position` | canvas position of the Begin pill |
| `mcps` | MCP server configs (documented; absent from all account flows) |
| `default_dynamic_variables` | documented flow field; already in our model |
| `is_published`, `is_transfer_cf` / `is_transfer_llm`, `flex_mode` | flags |

`tools[]` entries carry `tool_id`, `type` (`custom`), `name`, `description`,
`url`, `method`, `headers`, `query_params`, `parameters`, `parameter_type`,
`args_at_root`, `timeout_ms`, `response_variables`, `speak_after_execution`,
`speak_during_execution`, `enable_typing_sound`.

`components[]` (subflows) carry `conversation_flow_component_id`, `name`,
`nodes`, `start_node_id`, `tools`, `user_modified_timestamp`.

### Node types

Seven types are in scope for v1 ("Core 7") — meaning the runtime accepts a graph
containing them and the editor can author them. Six are verified against a
fixture; `extract_dynamic_variables` is pinned by Retell's OpenAPI schema alone.
`subagent` is in scope by executing as `conversation` (see
[Per-node behaviour](#per-node-behaviour)); it gets no dedicated runtime or
authoring affordance beyond that.

| Type | Fields |
|---|---|
| `conversation` | `instruction`, `edges`, `always_edge`, `skip_response_edge`, `start_speaker`, `interruption_sensitivity`, `name`, `id`, `display_position` |
| `branch` | `edges`, `else_edge`, `global_node_setting`, `name`, `id`, `display_position` |
| `function` | `tool_id`, `tool_type`, `edges`, `else_edge`, `speak_during_execution`, `wait_for_result`, `enable_typing_sound`, `name`, `id`, `display_position` |
| `transfer_call` | `transfer_destination`, `transfer_option`, `custom_sip_headers`, `ignore_e164_validation`, `edge`, `instruction`, `speak_during_execution`, `global_node_setting`, `name`, `id`, `display_position` |
| `end` | `instruction`, `speak_during_execution`, `name`, `id`, `display_position` |
| `subagent` | `instruction`, `edges`, `name`, `id`, `display_position` |
| `extract_dynamic_variables` | `variables` (array of `AnalysisData`), `edges`, `else_edge`, `enable_typing_sound`, `finetune_transition_examples`, `name`, `id`, `display_position` |

All node types additionally accept `finetune_transition_examples`, and
`conversation` accepts `finetune_conversation_examples` — stored and
round-tripped, but not consumed by the v1 runtime.

`begin` is not a node — it is `start_node_id` plus
`begin_tag_display_position`.

**`instruction`** is `{"type": "prompt" | "static_text", "text": "…"}`.
`prompt` means the model phrases it; `static_text` is spoken verbatim. This is
the "Prompt / Static Sentence" toggle in the dashboard.

**Edges** come in four shapes, all sharing
`{id, transition_condition, destination_node_id?}`:

- `edges[]` — conditional transitions.
- `else_edge` — guaranteed fallback on `branch` and `function`.
- `edge` — the single failure edge on `transfer_call`.
- `always_edge` / `skip_response_edge` — on `conversation`; unconditional next,
  and transition-without-speaking respectively.

**`transition_condition` has two forms**, and v1 supports both:

- `{"type": "prompt", "prompt": "…"}` — LLM-judged. This is the only form the
  account's flows use: all 63 transitions, including time-based branch
  conditions written as natural language
  (`"Current time {{current_time_America…}}"`).
- `{"type": "equation", "equations": [Equation], "operator": "||" | "&&"}` —
  deterministic, max 50 equations. Evaluated top-to-bottom, first true condition
  wins. Only dynamic variables may appear; anything requiring LLM extraction
  must use a prompt condition. Operators observed in the docs: `>`, `<`, `==`,
  `!=`, `CONTAINS`, `NOT CONTAINS`, `exists`, combined with `AND`/`OR`.

The mix matters for the runtime: equation edges cost nothing to evaluate, prompt
edges need the model. See [Per-node behaviour](#per-node-behaviour).

**Where node validation lives.** Nodes stay opaque JSON at the DB layer
(`models.py`), and the API validates nothing about node types — it stores
whatever it is handed, so importing a flow containing an `mcp` or `component`
node is lossless even though neither runs. Type enforcement belongs to the
worker, at call start, where an unsupported node is a startup error naming the
node id. `backend/` and `worker/` are separate uv projects with no shared
package, so there is no single module to put this in; the fixtures, read by both
test suites, are what keep the two aligned.

**Two shapes the parser must tolerate**, both present in older fixtures:

- edges carrying a legacy `condition` string alongside `transition_condition`;
- edges with no `destination_node_id` (dangling — authored but not connected).

**Global nodes** are `global_node_setting: {condition: "…"}` on the node —
reachable from anywhere without an edge.

### Not in v1

Out: `press_digit`, `agent_swap`, `sms`, `mcp`, `code`, `component`,
`bridge_transfer` and `cancel_transfer`. Each is new runtime surface —
`component` needs subflow-invocation semantics plus the separate
`conversation-flow-component` API, and `bridge_transfer`/`cancel_transfer` are
warm-transfer machinery.

A graph containing any of them is rejected at load with the offending node id
(see [Graph loading](#graph-loading)), never half-executed.

`note` is authoring-only and never reaches the worker.

## The fidelity gap

`CreateConversationFlowRequest` (`schemas_extra.py:60`) accepts seven fields:
`nodes`, `start_speaker`, `model_choice`, `global_prompt`, `start_node_id`,
`tools`, `default_dynamic_variables`. Real flows carry twelve more. Running the
migration script against the prior-auth flow today would silently discard its
subflow, its canvas notes, its knowledge-base config and its entire layout.

This violates the prime directive (extra fields fine, drops never) and is a bug
independent of this feature. The fix:

1. Add columns for `components`, `notes`, `kb_config`, `knowledge_base_ids`,
   `begin_tag_display_position`, `tool_call_strict_mode`, `is_transfer_cf`,
   `model_temperature` (JSON/scalar as appropriate), plus the corresponding
   request fields and serializer keys.
2. Add a fixture round-trip test asserting deep equality (below).

`flex_mode` and `is_published` are accepted and stored but carry no behaviour.

## Versioning

Publishing today snapshots the agent + Retell LLM rows into `agent_versions`;
published versions are immutable (`services/versions.py`). Flows sit outside
that system — the module docstring says so explicitly.

**Decision: snapshot the flow into the agent version.** `agent_versions` gains a
`conversation_flow` JSON column beside the LLM one; `_snapshot` / `_detach` /
`resolve` learn a third object. Publish freezes the graph; drafts keep writing
through `PATCH /update-conversation-flow` so no keystroke costs a snapshot.
Editing a draft can never affect a live call.

**Accepted divergence:** Retell versions flows independently — their
`/v2/list-conversation-flows` returns one row *per version* (v0…v32 observed),
with `is_published` on each. Ours returns one row per flow, latest only. No
consumer reads flow endpoints (`docs/RETELL_INTEGRATION_MAP.md`), so the
divergence is deliberate rather than a contract break, and it keeps one
versioning system instead of two. If a consumer ever needs per-version flow
reads, that is a follow-up.

## Runtime

New module `worker/src/arhiteq_worker/flow.py`. `main.py` changes in exactly one
place: where it computes `instructions` for a single-prompt call (`main.py:807`),
a flow-backed call builds a `FlowRuntime` and asks it for the entry node's
instructions and tools instead.

Transitions then reuse the primitives `agent_swap` already uses —
`agent.update_instructions()` and `agent.update_tools()` (`main.py:863`). No new
session machinery.

### Prompt assembly

Per node: `global_prompt` + the node's `instruction.text` (when
`instruction.type == "prompt"`) + a rendered list of that node's transition
conditions. `{{variable}}` templating runs through the existing
`resolve_template`. When `instruction.type == "static_text"` the worker speaks
the resolved text verbatim rather than prompting the model to phrase it.

### Transition mechanism

Equation edges never reach the model. On every transition point the runtime
first evaluates the node's `equation` edges in declaration order against the
live variable map; the first true one wins.

Only if no equation edge fires do the `prompt` edges go to the model, as a
single synthetic tool per node, `transition_to(edge_id)`, whose enum lists that
node's prompt-edge ids and whose description carries each condition's text. One
tool with an enum — not one tool per edge — so `tool_call_strict_mode` remains
meaningful. Global-node conditions are appended to every node's enum, which is
precisely their "jump here without an edge" semantic. Dangling edges are omitted
from the enum.

### Per-node behaviour

- **`conversation`** — converse, then transition. `always_edge` moves on
  unconditionally after a turn; `skip_response_edge` transitions without
  speaking. `start_speaker` and `interruption_sensitivity` override session
  defaults for that node.
- **`branch`** — speaks nothing; pure routing. Equation edges resolve in code
  with no model call at all, which is the common case for a branch. If the node
  has prompt edges and no equation matched, one cheap classification call
  (flash-lite) scores them against the transcript so far. No match routes to
  `else_edge`. This is the only extra LLM call in the design, and an
  all-equation branch avoids even that.
- **`extract_dynamic_variables`** — runs a structured extraction over the
  transcript using the node's `variables` spec, which is `AnalysisData` — the
  same shape as `post_call_analysis_data` (`schemas.py:146`), so it follows the
  pattern `services/analysis.py` already uses for post-call extraction, executed
  worker-side mid-call. Extracted values merge into the live variable map, which
  is what makes downstream equation conditions useful. Failure or empty
  extraction routes to `else_edge`.
- **`function`** — resolve `tool_id` against the flow's `tools[]` and hand the
  definition to the existing `build_tools()` (`tools.py:1011`), which already
  implements customer HTTP tools including flat-args and `X-Caller-Secret`.
  `speak_during_execution` emits a filler line; `wait_for_result: false` fires
  and continues; `response_variables` merge into the live variable map;
  failure routes to `else_edge`.
- **`transfer_call`** — the existing transfer built-in.
  `transfer_destination.type` is `predefined` (a number) or `inferred` (a
  prompt); `transfer_option.type` is `cold_transfer` or `warm_transfer`;
  `custom_sip_headers` pass through. Failure routes to the single `edge`.
- **`end`** — speak `instruction` when `speak_during_execution`, then the
  existing end-call path.
- **`subagent`** — **runs as `conversation`.** Its field set is a strict subset
  of `conversation`'s, so this is a faithful degradation rather than a stub, and
  it lets the 18-node prior-auth fixture run end to end on Core 6.

### Graph loading

`FlowGraph.from_config()` indexes `nodes` *and* every `components[].nodes` into
one id→node map, so any `destination_node_id` pointing into a subflow resolves.
Components are preserved verbatim on write.

What v1 does *not* implement is the `component` node — the documented
subflow-invocation type (`component_id`, `component_type: local | shared`).
Neither fixture contains one, and supporting it also means supporting the
separate `conversation-flow-component` API for shared components. Until then a
graph containing a `component` node is rejected at load like any other
unsupported type.

Validation runs once, at call start: an unsupported node type or an edge
pointing at a missing node raises immediately, naming the node id. A malformed
graph must never surface as a dead end ninety seconds into a call.

### Model mapping

Flows carry `model_choice` naming OpenAI models (`gpt-5.1` in the fixtures).
Arhiteq is Gemini-only, so flow model selection maps onto the Gemini catalogue
through the same path the single-prompt engine already uses. Per-node
`model_choice` overrides the flow default.

### Internal API

`GET /agent-config` and `_call_config` (`api/internal.py:57`) return a
`conversation_flow` object alongside `llm`, resolved at the correct agent
version. The worker never fetches flows directly.

## Editor

Route stays `/agents/[id]`; the page branches on `response_engine.type`. Flow
agents replace the prompt column with `<FlowEditor>` while keeping
`EditorHeader` (autosave chip, version dropdown, Publish) and the settings rail
— so versioning, publishing and the test panel are reused, not rebuilt.

Three panes, matching the Retell layout: node palette (left), canvas (centre),
Node Settings / Global Settings (right).

**Canvas: `@xyflow/react` (React Flow).** MIT, React 19 compatible. It supplies
pan/zoom, drag, edge routing, minimap and connection validation — the
interaction layer that would otherwise be ~1500 lines of in-house pointer and
transform code. This is a deliberate addition to a deliberately lean dependency
list (`livekit-client`, `lucide-react`, `next`, `react`, `react-dom`,
`recharts`).

**Nodes**: one component per type over a shared `NodeShell` (title bar, body,
handles). The four edge shapes render as visually distinct handles —
`edges[]` labelled with their condition text, `else_edge` as a muted fallback,
`edge` and `always_edge`/`skip_response_edge` as marked variants — so the graph
reads correctly instead of collapsing into one connector style.

**Condition editor**: each edge's condition switches between the two forms — a
textarea for `prompt`, and a row-per-equation builder (variable, operator,
comparand) with an `||` / `&&` combinator for `equation`. Equation rows offer
the flow's known dynamic variables, including any produced upstream by an
`extract_dynamic_variables` node or a tool's `response_variables`.

**Fidelity rule, client side:** the editor mutates a deep copy of the server's
flow JSON and never reconstructs it from a typed model. Unknown keys survive by
construction. With the backend fixture test, that is what stops `flex_mode`,
`is_transfer_cf`, or whatever Retell ships next month from being dropped.

**State and saving:** one flow object in a reducer, autosaved on the existing
800 ms debounce (`page.tsx:50`) via `PATCH /update-conversation-flow`, reusing
the `SaveState` chip. Node drags persist `display_position`; notes and
`begin_tag_display_position` persist the same way.

**Creation:** enable the disabled "Conversational flow" card
(`CreateAgentModal.tsx:166`) → `POST /create-conversation-flow` with a seed
begin+end graph, then `POST /create-agent` with
`response_engine: {type: "conversation-flow", conversation_flow_id}`. The agents
list gains "Conversation Flow" as an Agent Type value.

## Testing

- **Fidelity** — parametrized over the three fixtures: POST, GET, assert deep
  equality for every key *present in the fixture*, except `conversation_flow_id`,
  `version` and `last_modification_timestamp`. Keys the response adds (e.g.
  `default_dynamic_variables: null`, which Retell does not return) are ignored —
  the contract permits extra fields, never drops. This test fails before the
  schema widening; it is the regression guard against silent field drops.
- **Versioning** — publish a flow-backed agent, mutate the draft flow, resolve
  the published version, assert the graph is unchanged. Mirrors the existing LLM
  snapshot tests.
- **Worker** (`uv run --only-group dev pytest`, dev group only): graph parse and
  validation, per-node transition-tool construction, edge selection including
  `else_edge` fallback, `subagent`→`conversation` degradation, unknown node type
  raising at load, legacy `condition` and dangling-edge tolerance. Reads the same
  fixture files as the backend tests.
- **Equation evaluation** — its own table-driven unit suite, since it is the one
  piece of genuinely deterministic logic: every operator (`>`, `<`, `==`, `!=`,
  `CONTAINS`, `NOT CONTAINS`, `exists`), both combinators (`||`, `&&`),
  first-match-wins ordering, missing variables, and type coercion between string
  variables and numeric comparands. Hand-written cases, not fixture-derived —
  no account flow uses equations.
- **Frontend** — reducer round-trip (load fixture, no-op edit, serialize, deep
  equal) plus `bun run build`.
- **Manual** — the `/verify` recipe: local stack, create a flow agent, run a web
  call through the prior-auth graph end to end.

## Rollout

Schema changes ride the existing mechanism — no Alembic. New columns go into
`_COLUMN_BACKFILLS` (`main.py:93`) as idempotent `ALTER TABLE … ADD COLUMN`,
applied on boot next to `create_all`.

Three PRs, in order, each independently mergeable and each a conventional-commit
title (`main` is protected; squash merge):

1. **`feat(flows): full-fidelity conversation flow storage`** — columns, shared
   schema module, fixtures, fidelity test, `agent_versions` snapshot. Ships a
   real bug fix on its own.
2. **`feat(flows): conversation flow execution in the worker`** — `flow.py`,
   internal-API wiring, model mapping. Flow agents become runnable via the API.
3. **`feat(flows): conversation flow editor`** — React Flow canvas, node
   components, settings panel; the create-modal card is enabled *here*.

Gating the card until PR 3 means there is never a window in which the dashboard
can mint an agent that cannot take a call. The card is the feature flag; no
separate flag mechanism is needed.

## Follow-ups (not this spec)

- Flow support in the Simulation suite — `services/simulation.py:1171` builds a
  `retell-llm` engine and would need a flow path.
- The remaining node types: `component` (plus the `conversation-flow-component`
  API for shared subflows), `press_digit`, `agent_swap`, `sms`, `mcp`, `code`,
  `bridge_transfer` / `cancel_transfer`.
- Consuming `finetune_transition_examples` /
  `finetune_conversation_examples` at runtime; v1 stores them only.
- Native chat-agent creation (backend is already complete).
- Template catalogue expansion, including flow templates.
