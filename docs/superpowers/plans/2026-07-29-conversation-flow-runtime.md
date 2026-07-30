# Conversation Flow Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the voice worker actually run a conversation-flow agent — walk the node
graph during a live call, transitioning between nodes, calling tools, branching,
transferring and ending.

**Architecture:** A new `worker/src/arhiteq_worker/flow.py` holds the entire decision
layer as pure functions and one driver class. `main.py` changes in one place: where it
builds instructions for a single-prompt call, a flow-backed call builds a `FlowRuntime`
instead. Transitions reuse the two primitives `agent_swap` already uses —
`agent.update_instructions()` and `agent.update_tools()`.

**Tech Stack:** Python 3.14, livekit-agents, uv, pytest (`asyncio_mode` via
pytest-asyncio), Gemini (Vertex).

This is **plan 2 of 3** from
`docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md`. Plan 1 (PR #194)
made the control plane store flows losslessly and serve a version-correct
`conversation_flow` on the internal config endpoints. This plan consumes that key. Plan 3
is the dashboard editor.

**Branch:** `feat/conversation-flow-runtime`, stacked on `feat/conversation-flow-agents`
(plan 1) because it needs the `conversation_flow` config key. If PR #194 merges first,
rebase onto `main`.

## Global Constraints

- **Never rename or drop a wire field.** Extra fields fine; renames and drops are not.
  (`CLAUDE.md`, prime directive.)
- **The single-prompt path must not change behaviour.** It is the entire current
  product. A flow-backed call is selected only when `conversation_flow` is present in
  the config; everything else takes exactly today's path.
- **Testability rule, and it shapes every task:** the worker's CI test env installs the
  dev group only — no livekit stack. So all decision logic lives in module-level pure
  functions covered by ordinary tests, following
  `worker/tests/test_builtin_tools.py` ("Pure-logic tests for the Retell built-in tool
  helpers (no livekit stack)"). Anything that must touch livekit goes behind
  `pytest.importorskip("livekit.agents")`, as `worker/tests/test_tool_annotations.py:16`
  does. **A task whose logic can only be tested with livekit installed is a task with
  the wrong seams.**
- Worker tests: `cd worker && uv run --only-group dev pytest`.
- Python 3.14, uv, package `arhiteq_worker` under `worker/src/`, `pythonpath = ["src"]`.
- Arhiteq is Gemini-only. A flow's `model_choice` names OpenAI models (`gpt-5.1` in the
  fixtures); map onto the Gemini catalogue the way `_gemini_model()` already does for
  `llm.model`. Never send an OpenAI model id to a provider.
- Unsupported node type or unresolvable edge destination = **fail at call start**, naming
  the node id. Never a dead end mid-call.
- pre-commit runs gitleaks, ruff check + format, pytest (backend and worker), eslint.
- `main` is protected; PR title must be a conventional commit.

## Fixtures are shared with the backend

The three sanitized real Retell flows committed by plan 1 live at
`backend/tests/fixtures/retell_flows/*.json`. Worker tests read those same files — that
is deliberate, and it is what keeps the two projects' understanding of the schema
aligned. Add a small loader in `worker/tests/conftest.py`; resolve the path relative to
the repo root, and skip with a clear message if the directory is absent (a worker
checkout without the backend tree). **Never edit the fixtures.**

Node types present across them: `conversation`, `branch`, `function`, `transfer_call`,
`end`, `subagent`, plus flow-level `tools`, `components` and `notes`. No fixture contains
an `equation` condition, an `extract_dynamic_variables` node or a `component` node — the
first two are pinned by Retell's OpenAPI schema (see the spec) and need hand-written
tests; the third is out of scope.

## Design decisions this plan locks in

The spec describes the runtime at a level that leaves four things open. Deciding them
here, so no task has to invent one:

1. **A node's behaviour is implemented as the node's tools.** Entering a node sets the
   instructions and installs exactly the tools that node can use: its transition tool,
   plus (for a `function` node) that node's one HTTP tool, or (for
   `extract_dynamic_variables`) its extraction tool. The tool handler does the node's
   whole job and then advances the runtime. This keeps one mechanism instead of a
   parallel event system, and it matches how every existing Retell built-in is already
   wired in `tools.py`.
2. **Equation edges never reach the model.** On every transition point the runtime
   evaluates the current node's `equation` edges in declaration order first; the first
   true one wins. Only if none fires do `prompt` edges go to the model via the
   transition tool. A branch node whose edges are all equations therefore costs zero
   LLM calls.
3. **`branch` nodes with prompt edges use one cheap classification call.** A `branch`
   speaks nothing, so there is no model turn to attach a tool call to. The runtime makes
   a single non-streaming completion against the flow's mapped Gemini model, asking which
   edge condition the transcript so far satisfies. The call is made through an injected
   callable so every test can drive it without a provider.
4. **`subagent` executes as `conversation`.** Its field set is a strict subset. This is
   in the spec; it is restated here because it means the node-type dispatch table maps
   two type strings to one handler.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `worker/src/arhiteq_worker/config.py` | `ConversationFlowConfig` + `CallConfig.conversation_flow` | modify |
| `worker/src/arhiteq_worker/flow.py` | graph model, validation, equations, edge selection, prompt assembly, `FlowRuntime` | create |
| `worker/src/arhiteq_worker/main.py` | wire a flow-backed call to `FlowRuntime` | modify |
| `worker/tests/conftest.py` | shared Retell fixture loader | modify |
| `worker/tests/test_flow_config.py` | config parsing | create |
| `worker/tests/test_flow_graph.py` | parse / index / validate, real fixtures | create |
| `worker/tests/test_flow_equations.py` | equation evaluation, table-driven | create |
| `worker/tests/test_flow_transitions.py` | edge selection + prompt assembly | create |
| `worker/tests/test_flow_runtime.py` | driver behaviour with fakes | create |
| `worker/tests/test_flow_tools.py` | livekit tool wrapping (`importorskip`) | create |
| `docs/INTERNAL_API.md`, `docs/ARCHITECTURE.md`, `docs/API_COVERAGE.md` | documented behaviour | modify |

`flow.py` will be substantial. If it passes ~600 lines, split the pure decision layer
into `flow.py` and the driver into `flow_runtime.py` rather than letting one file sprawl.

---

### Task 1: Parse the flow out of the call config

**Files:**
- Modify: `worker/src/arhiteq_worker/config.py`
- Create: `worker/tests/test_flow_config.py`
- Modify: `worker/tests/conftest.py`

**Interfaces:**
- Produces `ConversationFlowConfig` (dataclass, `slots=True`, matching `LLMConfig`'s
  style) with: `global_prompt: str`, `nodes: list[dict]`, `start_node_id: str`,
  `start_speaker: str`, `tools: list[dict]`, `components: list[dict]`,
  `model_choice: dict | None`, `model_temperature: float | None`,
  `kb_config: dict | None`, `knowledge_base_ids: list[str]`,
  `default_dynamic_variables: dict`, `raw: dict`. Classmethod `from_dict`.
- Produces `CallConfig.conversation_flow: ConversationFlowConfig | None` — `None` for a
  single-prompt call. Later tasks branch on exactly this.
- Produces `load_retell_flow_fixture(name)` in `worker/tests/conftest.py`.

- [ ] **Step 1: Add the fixture loader**

In `worker/tests/conftest.py`, append:

```python
import json
from pathlib import Path

import pytest

# The sanitized real-Retell flow fixtures live in the backend tree and are the
# shared schema authority for both projects (see the design spec). Reading them
# from here is deliberate: if the two drift, these tests fail.
_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "retell_flows"


def load_retell_flow_fixture(name: str) -> dict:
    path = _FIXTURE_DIR / name
    if not path.is_file():
        pytest.skip(f"Retell flow fixture not available: {path}")
    return json.loads(path.read_text())


@pytest.fixture
def prior_auth_flow() -> dict:
    """18-node flow: every supported node type, plus components and notes."""
    return load_retell_flow_fixture("prior_auth_hotline.json")
```

- [ ] **Step 2: Write the failing test**

Create `worker/tests/test_flow_config.py`:

```python
"""Conversation-flow config parsing (no livekit stack)."""

from arhiteq_worker.config import CallConfig


def test_single_prompt_call_has_no_flow() -> None:
    cfg = CallConfig.from_dict({"call_id": "c1", "llm": {"general_prompt": "hi"}})
    assert cfg.conversation_flow is None


def test_flow_is_parsed_when_present(prior_auth_flow) -> None:
    cfg = CallConfig.from_dict({"call_id": "c1", "conversation_flow": prior_auth_flow})
    flow = cfg.conversation_flow
    assert flow is not None
    assert flow.start_node_id == prior_auth_flow["start_node_id"]
    assert len(flow.nodes) == len(prior_auth_flow["nodes"])
    assert flow.global_prompt == prior_auth_flow["global_prompt"]
    assert flow.tools == prior_auth_flow["tools"]
    assert flow.components == prior_auth_flow["components"]
    # Unknown/extra keys survive on raw, same contract as the other configs.
    assert flow.raw == prior_auth_flow


def test_flow_tolerates_a_minimal_object() -> None:
    cfg = CallConfig.from_dict({"call_id": "c1", "conversation_flow": {"nodes": []}})
    flow = cfg.conversation_flow
    assert flow is not None
    assert flow.nodes == []
    assert flow.start_node_id == ""
    assert flow.start_speaker == "agent"
    assert flow.tools == []
    assert flow.components == []
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_config.py -v
```

Expected: FAIL — `AttributeError: 'CallConfig' object has no attribute 'conversation_flow'`.

- [ ] **Step 4: Implement `ConversationFlowConfig`**

In `worker/src/arhiteq_worker/config.py`, after `LLMConfig`, add a dataclass in the same
style (`@dataclass(slots=True)`, a `from_dict` classmethod, the `_str`/`_num` coercion
helpers already in the module, list comprehensions that drop non-dict entries the way
`LLMConfig.general_tools` does). Fields exactly as named in this task's Interfaces block.
`start_speaker` defaults to `"agent"`; `start_node_id` falls back to the first node's
`id` when absent, mirroring what the backend's create handler does.

- [ ] **Step 5: Add the field to `CallConfig`**

Add `conversation_flow: ConversationFlowConfig | None = None` to `CallConfig` and, in
`from_dict`:

```python
            conversation_flow=(
                ConversationFlowConfig.from_dict(d["conversation_flow"])
                if isinstance(d.get("conversation_flow"), dict)
                else None
            ),
```

Note `CallConfig` uses `slots=True` and has a field with a default (`raw`) — keep field
ordering valid.

- [ ] **Step 6: Run the tests**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_config.py -v
cd worker && uv run --only-group dev pytest
```

Expected: new file passes; the whole dev suite still passes.

- [ ] **Step 7: Commit**

```bash
git add worker/src/arhiteq_worker/config.py worker/tests/test_flow_config.py worker/tests/conftest.py
git commit -m "feat(worker): parse the conversation flow out of the call config"
```

---

### Task 2: Graph model, indexing and validation

**Files:**
- Create: `worker/src/arhiteq_worker/flow.py`
- Create: `worker/tests/test_flow_graph.py`

**Interfaces:**
- `SUPPORTED_NODE_TYPES: frozenset[str]` = `{"conversation", "subagent", "branch",
  "function", "transfer_call", "end", "extract_dynamic_variables"}`.
- `class FlowError(Exception)` — raised for any unusable graph; `main.py` lets it abort
  the call at start.
- `class FlowGraph` with:
  - `classmethod from_config(flow: ConversationFlowConfig) -> FlowGraph` — indexes
    `nodes` **and** every `components[].nodes` into one id→node dict, validates, returns.
  - `node(node_id: str) -> dict` — raises `FlowError` on unknown id.
  - `start: dict` — the start node.
  - `global_nodes: list[dict]` — nodes carrying `global_node_setting.condition`.
- Later tasks consume all of the above.

- [ ] **Step 1: Write the failing tests**

Create `worker/tests/test_flow_graph.py`:

```python
"""Flow graph parsing, indexing and validation (no livekit stack)."""

import pytest

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import FlowError, FlowGraph


def _graph(flow_dict: dict) -> FlowGraph:
    return FlowGraph.from_config(ConversationFlowConfig.from_dict(flow_dict))


def test_indexes_every_node_by_id(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    for node in prior_auth_flow["nodes"]:
        assert graph.node(node["id"])["type"] == node["type"]


def test_start_node_is_the_configured_one(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    assert graph.start["id"] == prior_auth_flow["start_node_id"]


def test_component_nodes_are_reachable_by_id(prior_auth_flow) -> None:
    """A destination_node_id may point into a subflow; it must still resolve."""
    component_nodes = [n for c in prior_auth_flow["components"] for n in c["nodes"]]
    assert component_nodes, "fixture is expected to carry a component"
    graph = _graph(prior_auth_flow)
    for node in component_nodes:
        assert graph.node(node["id"])["id"] == node["id"]


def test_global_nodes_are_collected(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    expected = {
        n["id"] for n in prior_auth_flow["nodes"] if (n.get("global_node_setting") or {}).get("condition")
    }
    assert {n["id"] for n in graph.global_nodes} == expected


def test_unsupported_node_type_is_rejected_at_load() -> None:
    with pytest.raises(FlowError, match="node-mcp"):
        _graph({
            "start_node_id": "a",
            "nodes": [
                {"id": "a", "type": "conversation", "instruction": {"type": "prompt", "text": "hi"}},
                {"id": "node-mcp", "type": "mcp", "mcp_id": "m1", "mcp_tool_name": "t"},
            ],
        })


def test_edge_to_a_missing_node_is_rejected_at_load() -> None:
    with pytest.raises(FlowError, match="ghost"):
        _graph({
            "start_node_id": "a",
            "nodes": [{
                "id": "a",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "hi"},
                "edges": [{
                    "id": "e1",
                    "transition_condition": {"type": "prompt", "prompt": "x"},
                    "destination_node_id": "ghost",
                }],
            }],
        })


def test_dangling_edge_without_a_destination_is_allowed() -> None:
    """Authored-but-unconnected edges exist in real flows; they must not abort a call."""
    graph = _graph({
        "start_node_id": "a",
        "nodes": [{
            "id": "a",
            "type": "conversation",
            "instruction": {"type": "prompt", "text": "hi"},
            "always_edge": {"id": "e1", "transition_condition": {"type": "prompt", "prompt": "Always"}},
        }],
    })
    assert graph.start["id"] == "a"


def test_missing_start_node_is_rejected() -> None:
    with pytest.raises(FlowError):
        _graph({"start_node_id": "nope", "nodes": [{"id": "a", "type": "end"}]})


def test_empty_graph_is_rejected() -> None:
    with pytest.raises(FlowError):
        _graph({"nodes": []})


def test_every_real_fixture_loads(request) -> None:
    from tests.conftest import load_retell_flow_fixture

    for name in ("prior_auth_hotline.json", "clara_outbound.json", "identity_verify_transfer.json"):
        graph = _graph(load_retell_flow_fixture(name))
        assert graph.start is not None
```

- [ ] **Step 2: Run and confirm failure**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_graph.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'arhiteq_worker.flow'`.

- [ ] **Step 3: Implement**

Create `worker/src/arhiteq_worker/flow.py` with a module docstring explaining that this
module is the decision layer and holds no livekit imports, so it is testable in the
dev-only env. Implement `SUPPORTED_NODE_TYPES`, `FlowError` and `FlowGraph` per the
Interfaces block.

Validation rules, all raising `FlowError` with the offending node id in the message:
- empty node set, or a `start_node_id` that resolves to nothing;
- any node whose `type` is not in `SUPPORTED_NODE_TYPES`;
- any edge whose `destination_node_id` is present but resolves to no node.

An edge with **no** `destination_node_id` is valid and must not raise — real flows carry
them.

Collect edges from all five shapes when validating destinations: `edges[]`, `else_edge`,
`edge`, `always_edge`, `skip_response_edge`. Write one small helper that yields every
edge of a node so no later task has to remember the list; export it, later tasks use it.

- [ ] **Step 4: Run and commit**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_graph.py -v
cd worker && uv run --only-group dev pytest
git add worker/src/arhiteq_worker/flow.py worker/tests/test_flow_graph.py
git commit -m "feat(worker): index and validate a conversation flow graph"
```

---

### Task 3: Equation conditions

Deterministic, no model involved. This is the one piece of genuinely computational logic
in the runtime, so it gets its own table-driven suite. No fixture contains an equation —
every case here is hand-written from Retell's documented syntax.

**Files:**
- Modify: `worker/src/arhiteq_worker/flow.py`
- Create: `worker/tests/test_flow_equations.py`

**Interfaces:**
- `evaluate_equation_condition(condition: dict, variables: Mapping[str, Any]) -> bool` —
  `condition` is `{"type": "equation", "equations": [...], "operator": "||" | "&&"}`.
  Returns False (never raises) for a malformed condition.

- [ ] **Step 1: Write the failing tests**

Create `worker/tests/test_flow_equations.py`. Start from this, which fixes the shape and
the subtle cases:

```python
"""Equation transition conditions (no livekit stack).

No account fixture contains an `equation` condition, so every case here is
hand-written from Retell's documented syntax. The per-equation shape
({"left", "operator", "right"}) is OUR READING of their OpenAPI schema, which
pins only `equations` and `operator` — if a real flow ever contradicts it, this
file and `evaluate_equation_condition` are the two places to change.
"""

import pytest

from arhiteq_worker.flow import evaluate_equation_condition


def _cond(*equations, operator="&&"):
    return {"type": "equation", "equations": list(equations), "operator": operator}


def _eq(left, operator, right):
    return {"left": left, "operator": operator, "right": right}


VARS = {"user_age": "21", "user_location": "New York", "empty": ""}


@pytest.mark.parametrize(
    ("equation", "expected"),
    [
        # Variables arrive as strings; a numeric comparand must still compare numerically.
        (_eq("{{user_age}}", ">", 18), True),
        (_eq("{{user_age}}", "<", 18), False),
        (_eq("{{user_location}}", "==", "New York"), True),
        (_eq("{{user_location}}", "!=", "New York"), False),
        # Reversed form from the docs: literal list CONTAINS a variable.
        (_eq("New York, Los Angeles", "CONTAINS", "{{user_location}}"), True),
        (_eq("New York, Los Angeles", "NOT CONTAINS", "{{user_location}}"), False),
        (_eq("{{user_location}}", "exists", None), True),
        (_eq("{{empty}}", "exists", None), False),
        (_eq("{{never_set}}", "exists", None), False),
        # A numeric operator against a non-numeric string is False, not an error.
        (_eq("{{user_location}}", ">", 18), False),
        # A missing variable is False for every operator.
        (_eq("{{never_set}}", "==", "New York"), False),
        (_eq("{{never_set}}", ">", 1), False),
    ],
)
def test_single_equation(equation, expected) -> None:
    assert evaluate_equation_condition(_cond(equation), VARS) is expected


def test_and_requires_every_equation() -> None:
    both = _cond(_eq("{{user_age}}", ">", 18), _eq("{{user_location}}", "==", "New York"))
    assert evaluate_equation_condition(both, VARS) is True
    one = _cond(_eq("{{user_age}}", ">", 18), _eq("{{user_location}}", "==", "Boston"))
    assert evaluate_equation_condition(one, VARS) is False


def test_or_requires_any_equation() -> None:
    cond = _cond(
        _eq("{{user_age}}", "<", 18),
        _eq("{{user_location}}", "==", "New York"),
        operator="||",
    )
    assert evaluate_equation_condition(cond, VARS) is True


@pytest.mark.parametrize(
    "condition",
    [
        {"type": "equation", "equations": [], "operator": "&&"},
        {"type": "equation", "equations": [_eq("{{user_age}}", ">", 1)]},  # no operator
        {"type": "equation"},
        {"type": "prompt", "prompt": "not an equation"},
        None,
        "nonsense",
    ],
)
def test_malformed_conditions_are_false_and_never_raise(condition) -> None:
    assert evaluate_equation_condition(condition, VARS) is False


def test_many_equations_do_not_raise() -> None:
    cond = _cond(*[_eq("{{user_age}}", ">", 1)] * 60, operator="&&")
    assert evaluate_equation_condition(cond, VARS) in (True, False)
```

Beyond the code above, add one case proving a `{{current_time}}`-style system variable
resolves inside an equation (the fixtures' branch conditions are time-based), using a
variables mapping built the way `CallConfig.resolution_variables()` builds one.

- [ ] **Step 2: Run and confirm failure**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_equations.py -v
```

Expected: ImportError for `evaluate_equation_condition`.

- [ ] **Step 3: Implement**

Add to `flow.py`. Resolve `{{var}}` operands through the existing
`arhiteq_worker.variables.resolve_template` so system variables
(`{{current_time}}`) work exactly as they do in prompts. Coerce both sides to float when
both look numeric, else compare as strings. `exists` is true when the resolved value is
a non-empty string. Never raise: a malformed equation is False, and log at debug.

- [ ] **Step 4: Run and commit**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_equations.py -v
git add worker/src/arhiteq_worker/flow.py worker/tests/test_flow_equations.py
git commit -m "feat(worker): evaluate equation transition conditions"
```

---

### Task 4: Edge selection and prompt assembly

**Files:**
- Modify: `worker/src/arhiteq_worker/flow.py`
- Create: `worker/tests/test_flow_transitions.py`

**Interfaces:**
- `select_equation_edge(node, variables) -> dict | None` — first `equation` edge whose
  condition is true, in declaration order; `None` if none fires.
- `prompt_edges(node, global_nodes) -> list[dict]` — the node's `prompt`-condition edges
  plus one synthetic edge per global node, each with a stable `id`. Dangling edges
  (no `destination_node_id`) are excluded.
- `fallback_edge(node) -> dict | None` — `else_edge`, or `edge` for `transfer_call`,
  else `None`.
- `node_instructions(node, flow, variables) -> str` — `global_prompt`, then the node's
  `instruction.text` when `instruction.type == "prompt"`, then a rendered transition
  list. Everything `resolve_template`d.
- `static_text(node, variables) -> str | None` — the resolved verbatim line when
  `instruction.type == "static_text"`, else `None`.
- `transition_tool_schema(node, edges) -> dict` — the raw JSON schema for
  `transition_to`, an enum of edge ids with each condition's prompt text in the
  description. Returns `None` when there are no prompt edges (nothing to ask the model).

- [ ] **Step 1: Write the failing tests**

Create `worker/tests/test_flow_transitions.py` covering:

- equation edges win over prompt edges, and declaration order decides among equations;
- `prompt_edges` returns the node's prompt edges, excludes dangling ones, and appends one
  entry per global node;
- `fallback_edge` returns `else_edge` for `branch` and `function`, the single `edge` for
  `transfer_call`, `None` for a bare `conversation`;
- `node_instructions` starts with the flow's `global_prompt`, contains a `prompt`-type
  node's instruction text, and resolves `{{variables}}`;
- `node_instructions` for a `static_text` node does **not** ask the model to phrase
  anything, and `static_text` returns the resolved line;
- `transition_tool_schema` produces one enum entry per prompt edge with the condition
  text reachable in the description, and returns `None` for a node with no prompt edges;
- run `node_instructions` over every `conversation` node of the real prior-auth fixture
  and assert it never raises and never leaves an unresolved `{{`  in a node whose
  variables are all supplied.

- [ ] **Step 2: Run, confirm failure, implement, run again**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_transitions.py -v
```

Implement in `flow.py`. Reuse the all-edges helper from Task 2. The rendered transition
list should name each edge id beside its condition so the model's tool call is
unambiguous.

- [ ] **Step 3: Commit**

```bash
git add worker/src/arhiteq_worker/flow.py worker/tests/test_flow_transitions.py
git commit -m "feat(worker): select flow edges and assemble per-node instructions"
```

---

### Task 5: The runtime driver

**Files:**
- Modify: `worker/src/arhiteq_worker/flow.py`
- Create: `worker/tests/test_flow_runtime.py`

**Interfaces:**
- `class FlowRuntime`, constructed with the graph, the flow config, the live variables
  mapping, and a set of **injected callables** so no test needs livekit or a provider:
  - `set_instructions: Callable[[str], Awaitable[None]]`
  - `set_tools: Callable[[list[Any]], Awaitable[None]]`
  - `say: Callable[[str], Awaitable[None]]`
  - `classify: Callable[[str, list[dict]], Awaitable[str | None]]` — returns the chosen
    edge id for a branch node's prompt edges
  - `build_node_tools: Callable[[dict, list[dict]], list[Any]]` — wraps this node's
    transition tool plus any node-specific tool; the livekit-touching part, injected so
    the driver stays pure
  - `end_call: Callable[[str], Awaitable[None]]`
  - `transfer_call: Callable[[str], Awaitable[str]]`
- `async def start() -> None` — enter the start node.
- `async def advance(edge: dict) -> None` — follow an edge to its destination and enter it.
- `async def on_user_turn() -> None` — re-evaluate equation edges (and `always_edge`) for
  the current node; called from the session's user-turn hook.
- `current_node_id: str` — for logging and tests.

- [ ] **Step 1: Write the failing tests**

Create `worker/tests/test_flow_runtime.py` using simple recording fakes for every
injected callable (append to a list; return canned values). Cover:

- entering a `conversation` node sets instructions containing that node's text and
  installs tools;
- a `static_text` node speaks the line verbatim via `say` and does not ask the model to
  phrase it;
- an `end` node calls `end_call`, and speaks its instruction first when
  `speak_during_execution` is true;
- a `transfer_call` node calls `transfer_call` with a `predefined` destination number,
  and on failure follows the node's single `edge`;
- a `branch` node with only equation edges routes with **zero** `classify` calls;
- a `branch` node with prompt edges calls `classify` exactly once and follows the edge it
  names;
- a `branch` whose `classify` returns `None` (no match) follows `else_edge`;
- a `subagent` node behaves exactly like a `conversation` node — assert the same
  instructions/tools calls as the equivalent conversation node;
- **`skip_response_edge` speaks, then advances without waiting for the caller.**
  ("Skip response" = skip waiting for *their* reply, not skip saying ours. All four
  prior-auth nodes carrying one are `static_text` with an empty `edges[]`, so the skip
  edge is their only exit — the other reading would make those nodes exist solely to not
  say their own line.) Assert the line IS spoken and that the destination is entered with
  no user turn in between, and that such a chain stops at `MAX_AUTOMATIC_TRANSITIONS`
  instead of spinning;
- **`always_edge` fires on the next user turn** without needing a model decision, and is
  not offered to the model as a prompt edge;
- walking the real prior-auth fixture from its start node through a scripted sequence of
  edge choices never raises and ends on an `end` node;
- an equation edge that becomes true after a variable is updated causes `on_user_turn`
  to transition.

- [ ] **Step 2: Run, confirm failure, implement, run again**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_runtime.py -v
cd worker && uv run --only-group dev pytest
```

Implement `FlowRuntime` in `flow.py` (or `flow_runtime.py` if `flow.py` is over ~600
lines). Node-type dispatch as a dict mapping type string → handler coroutine, with
`conversation` and `subagent` mapped to the same handler. Log each transition at info
with call id, from-node and to-node — a flow call is otherwise very hard to debug from
logs alone.

- [ ] **Step 3: Commit**

```bash
git add worker/src/arhiteq_worker/flow.py worker/tests/test_flow_runtime.py
git commit -m "feat(worker): drive a conversation flow through a live call"
```

---

### Task 6: The livekit tool layer

The thin part that must import livekit. Everything hard was decided in Tasks 3–5.

**Files:**
- Modify: `worker/src/arhiteq_worker/flow.py`
- Create: `worker/tests/test_flow_tools.py`

**Interfaces:**
- `make_transition_tool(schema, on_transition) -> Any` — wraps
  `transition_tool_schema`'s output with `function_tool(handler, raw_schema=schema)`,
  following `tools.py`'s existing constructors exactly (see `_make_transfer_call_tool`,
  `tools.py:441`). The handler records the invocation on `CallState` the way the other
  tools do, then calls `on_transition(edge_id)`.
- `make_function_node_tool(...)` — for a `function` node: resolves `tool_id` against the
  flow's `tools[]`, executes via the already-tested `safe_execute_custom_tool` from
  `tools.py`, merges the entry's `response_variables` into the live variables, then
  advances. Honour `speak_during_execution` (filler line) and `wait_for_result: false`
  (advance without waiting).
- `make_extract_node_tool(...)` — for `extract_dynamic_variables`: build parameters with
  the **existing** `extract_variable_parameters()` from `tools.py` (it already converts a
  Retell variable spec into JSON-schema parameters), merge results into the live
  variables and `state.collected_dynamic_variables`, then advance.
- **Knowledge base:** when the flow carries `knowledge_base_ids`, every `conversation`
  and `subagent` node also gets a `kb_lookup` tool. Reuse the existing
  `_make_kb_lookup_tool` (`tools.py:952`) rather than writing a second one, and pass the
  flow's `kb_config` (`{top_k, filter_score}`) through. A flow with no knowledge bases
  gets no such tool, exactly as a single-prompt agent without them does.

- [ ] **Step 1: Write the tests**

Create `worker/tests/test_flow_tools.py` beginning with:

```python
"""Flow tool wrapping. Requires the livekit stack — skipped in the dev-only env."""

import pytest

pytest.importorskip("livekit.agents")
```

Mirror `worker/tests/test_tool_annotations.py`, which exists precisely because
livekit-agents calls `typing.get_type_hints()` on every function tool and a bad
annotation only explodes mid-call. Assert that each flow tool constructor produces a tool
whose annotations resolve, whose schema name is what the runtime expects, and that
invoking the transition tool's handler calls back with the chosen edge id.

- [ ] **Step 2: Run**

```bash
cd worker && uv run --only-group dev pytest tests/test_flow_tools.py -v
```

Expected in the dev-only env: **skipped**. That is correct, not a failure. To actually
exercise them: `cd worker && uv sync && uv run pytest tests/test_flow_tools.py -v`. Do
that once before committing and report both results.

- [ ] **Step 3: Implement, re-run, commit**

```bash
git add worker/src/arhiteq_worker/flow.py worker/tests/test_flow_tools.py
git commit -m "feat(worker): wrap flow node behaviours as livekit tools"
```

---

### Task 7: Wire it into the entrypoint

**Files:**
- Modify: `worker/src/arhiteq_worker/main.py`

**Interfaces:**
- Consumes everything above. Produces no new public surface.

- [ ] **Step 1: Read the current shape**

The single-prompt path builds `livekit_tools` (`main.py:794`), then
`instructions = resolve_template(cfg.llm.general_prompt, variables)` (`main.py:807`),
then `begin_message`, then `build_session`, then `ArhiteqAgent(...)` (`main.py:824`).
`_do_agent_swap` (`main.py:831`) shows the update pattern:
`await agent.update_instructions(...)` and `await agent.update_tools(...)`.

- [ ] **Step 2: Branch on the flow**

When `cfg.conversation_flow is not None`:
- build the `FlowGraph` — a `FlowError` here must abort the call at start, before the
  greeting, with the node id in the log;
- construct `FlowRuntime`, injecting: `set_instructions` → `agent.update_instructions`;
  `set_tools` → `agent.update_tools`; `say` → `session.say`; `end_call` →
  `runtime.end_call`; `transfer_call` → `runtime.transfer_call`; `classify` → a small
  helper that makes one non-streaming completion against the mapped Gemini model;
  `build_node_tools` → the Task 6 constructors, closed over `tool_http`,
  `cfg.function_secret`, `variables`, `state` and the flow's `tools[]`;
- the agent starts with the **start node's** instructions and tools rather than
  `cfg.llm.general_prompt`;
- `start_speaker` comes from the flow (and the start node's own override when present);
- call `flow_runtime.start()` after `session.start`, and wire `on_user_turn` into the
  existing session event plumbing (`_wire_session_events`, `main.py:555`).

Map `model_choice` onto the Gemini catalogue through the same helper `_gemini_model`
already uses for `cfg.llm.model`; a flow's `gpt-5.1` must never be sent to a provider.

**Leave the single-prompt path byte-for-byte unchanged.** Prefer adding a branch over
refactoring the shared prologue.

- [ ] **Step 3: Verify nothing regressed**

```bash
cd worker && uv run --only-group dev pytest
```

Expected: all pass. Then, with the full stack: `cd worker && uv sync && uv run pytest`.
Report both.

- [ ] **Step 4: Commit**

```bash
git add worker/src/arhiteq_worker/main.py
git commit -m "feat(worker): run flow-backed agents through the flow runtime"
```

---

### Task 8: Live verification

Automated tests cannot prove a real call walks the graph. This task is manual and its
deliverable is evidence.

- [ ] **Step 1: Read the recipe**

Follow the repo's `/verify` skill (`.claude/skills/verify`) for bringing up the local
stack: `docker compose up -d`, then `make api`, `make worker`, `make web`.

- [ ] **Step 2: Create a flow-backed agent**

Import the prior-auth fixture through the API (`POST /create-conversation-flow` with the
fixture body), then `POST /create-agent` with
`response_engine: {"type": "conversation-flow", "conversation_flow_id": "<id>"}`. Publish
it.

- [ ] **Step 3: Drive a real web call**

Use the dashboard's Test panel. Walk at least: the welcome `conversation` node → a
transition into the subagent/conversation branch → one `function` node → an `end` node.

- [ ] **Step 4: Record the evidence**

In the report: the worker's transition log lines, the resulting call's
`transcript_with_tool_calls`, and confirmation that the call ended cleanly with a
`call_ended` webhook. Note anything that behaved differently from the plan.

- [ ] **Step 5: Stop the stack**

Bring down the compose stack and kill the dev servers when done — do not leave them
running.

---

### Task 9: Docs and PR

- [ ] **Step 1: Update the docs**

- `docs/INTERNAL_API.md` — the `conversation_flow` object on the call/agent config
  endpoints, and that the worker consumes it.
- `docs/ARCHITECTURE.md` — how a flow call executes: per-node instructions, the
  transition tool, equation-before-prompt evaluation, the branch classification call.
- `docs/API_COVERAGE.md` — the Conversation flow row's note now that execution exists;
  say plainly which node types run and which are rejected at load.

State the supported node set and the unsupported ones explicitly. Someone will point a
`press_digit` flow at this and needs to know why the call refuses to start.

- [ ] **Step 2: Full verification**

```bash
cd backend && uv run pytest
cd worker && uv run --only-group dev pytest
cd .. && pre-commit run --all-files
```

Report each. Note that `pre-commit run --all-files` has pre-existing EXE001 failures in
`scripts/` files untouched by this branch; anything else is yours.

- [ ] **Step 3: Push and open the PR**

Base the PR on `feat/conversation-flow-agents` if PR #194 is still open, or rebase onto
`main` if it has merged. Title: `feat(flows): conversation flow execution in the worker`.

Body should cover: which node types execute, the equation-before-prompt rule, that
`subagent` runs as `conversation`, that unsupported nodes fail at call start rather than
mid-call, and the live-verification evidence from Task 8.

---

## What this plan does NOT do

- **No editor.** Flows are still authored via the API only. Plan 3.
- **No `press_digit`, `agent_swap`, `sms`, `mcp`, `code`, `component`,
  `bridge_transfer` or `cancel_transfer` nodes.** A graph containing one is rejected at
  call start, deliberately and loudly.
- **No subflow invocation.** `components[]` nodes are indexed so edges into them resolve,
  but there is no `component` node type to call one as a unit.
- **No Simulation support.** `services/simulation.py` builds a `retell-llm` engine; flow
  agents in the simulation suite are a separate spec.
- **No `finetune_transition_examples` / `finetune_conversation_examples`.** Stored by
  plan 1, ignored here.
- **No per-node overrides beyond `start_speaker`.** A node's
  `interruption_sensitivity`, and the per-node voice-speed / response-eagerness /
  reminder-frequency / LLM overrides visible in Retell's Node Settings panel, are parsed
  and ignored. Only the flow-level `model_choice` is honoured. Worth doing next; changing
  interruption sensitivity mid-session needs a session-level knob this runtime does not
  reach for yet.
