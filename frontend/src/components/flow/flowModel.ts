/**
 * The editor's client-side flow document: types, the reducer, and small pure
 * helpers every later task (canvas, node inspector, palette) builds on.
 *
 * FIDELITY IS THE WHOLE POINT. The editor never rebuilds a flow from a typed
 * model — it deep-copies the server's raw JSON (`RawConversationFlow` from
 * `@/lib/api`, itself an open shape) and mutates the copy in place. Every
 * type here that describes flow *content* (as opposed to editor-only action
 * payloads) carries a `& Record<string, unknown>` tail so an unknown key —
 * `flex_mode`, `is_transfer_cf`, whatever Retell ships next — survives a
 * round trip through this module untouched. See
 * `frontend/src/components/flow/__tests__/flowModel.test.ts`'s `fidelity`
 * suite, which loads real sanitized Retell captures from
 * `backend/tests/fixtures/retell_flows/` and asserts byte-for-byte
 * round-tripping through the reducer.
 *
 * The edge-shape handling here (`EDGE_SHAPES`, `iterNodeEdges`) mirrors
 * `worker/src/arhiteq_worker/flow.py`'s `_SINGLE_EDGE_FIELDS` /
 * `iter_node_edges` exactly: this editor's output has to be a graph that
 * worker can load without raising `FlowError`, and `NODE_TYPES` is the same
 * eight types as its `SUPPORTED_NODE_TYPES` — a ninth type in the palette
 * would produce a graph the worker rejects at call start.
 */

import type { RawConversationFlow } from "@/lib/api";
import { DEFAULT_FLOW_MODEL } from "@/lib/models";

// ---------------------------------------------------------------------------
// Types — every one open (`& Record<string, unknown>`) so an unknown key
// carried on real Retell flow content is never silently dropped.
// ---------------------------------------------------------------------------

export type Position = { x: number; y: number };

/** One `{left, operator, right}` comparand of an `equation` transition condition. */
export type Equation = {
  left?: unknown;
  operator?: string;
  right?: unknown;
} & Record<string, unknown>;

/** A node's or edge's transition condition: `{type: "prompt" | "equation", ...}`. */
export type TransitionCondition = {
  type?: string;
  prompt?: string;
  equations?: Equation[];
  operator?: "&&" | "||";
} & Record<string, unknown>;

export type FlowNode = { id: string; type: string } & Record<string, unknown>;

export type FlowEdge = {
  id: string;
  destination_node_id?: string;
  transition_condition?: TransitionCondition;
} & Record<string, unknown>;

/**
 * The five shapes a node's edges can be spread across, in the exact order
 * `iterNodeEdges` yields them. Must match
 * `worker/src/arhiteq_worker/flow.py:_SINGLE_EDGE_FIELDS` prefixed by
 * `"edges"` — that module is the runtime this editor's output has to load
 * into, and its ordering is asserted in the worker's own tests too.
 */
export type EdgeShape = "edges" | "else_edge" | "edge" | "always_edge" | "skip_response_edge";

export const EDGE_SHAPES: readonly EdgeShape[] = [
  "edges",
  "else_edge",
  "edge",
  "always_edge",
  "skip_response_edge",
];

/** The single-object edge fields, in the order they are checked after `edges[]`. */
const SINGLE_EDGE_SHAPES: readonly EdgeShape[] = EDGE_SHAPES.filter(
  (shape): shape is EdgeShape => shape !== "edges",
);

/**
 * The eight node types the worker knows how to execute
 * (`SUPPORTED_NODE_TYPES` in `flow.py`), in palette display order. A ninth
 * type must never be offered here: the worker rejects a graph containing one
 * at call start.
 */
export const NODE_TYPES: readonly string[] = [
  "conversation",
  "branch",
  "function",
  "extract_dynamic_variables",
  "transfer_call",
  "subagent",
  "press_digit",
  "end",
];

/**
 * The edge shape a wire dragged FROM a node of *nodeType* should be written
 * into, or `null` for a node the worker never follows an outgoing edge from.
 *
 * Not every node reads `edges[]`. `_enter_end` hangs the call up without
 * looking at any edge, and `_enter_transfer_call` consults only its single
 * `edge` (the failure fallback) — so a wire dragged off either used to be
 * written as an `edges[]` entry and drawn as a solid, labelled connector the
 * runtime would never follow. `NodeShell` hides the source handle entirely
 * where this returns `null`, so the impossible connection cannot be started
 * in the first place.
 */
export function connectShapeFor(nodeType: string): EdgeShape | null {
  if (nodeType === "end") return null;
  if (nodeType === "transfer_call") return "edge";
  return "edges";
}

/**
 * Every `equation` comparison operator, spelled the way the wire format
 * spells it: this is verbatim the `Equation.operator` enum from Retell's
 * create-conversation-flow schema, so a flow authored here is byte-compatible
 * with one exported from Retell.
 *
 * It is also exactly what the worker implements
 * (`worker/src/arhiteq_worker/flow.py`'s `_NUMERIC_COMPARISONS` |
 * `_EQUALITY_OPERATORS` | `_CONTAINMENT_OPERATORS` | `_UNARY_OPERATORS`), and
 * must never drift ahead of it: an operator offered here that the worker does
 * not recognize produces an edge whose condition silently evaluates to `False`
 * forever (`_evaluate_single_equation`'s "unrecognized operator"
 * fallthrough).
 *
 * This list used to read `CONTAINS` / `NOT CONTAINS` — the *display* syntax
 * from Retell's prose docs, not the wire format — so the editor emitted
 * operators a real Retell flow never carries. The worker still accepts that
 * spelling for anything already authored (`_OPERATOR_SYNONYMS`), but nothing
 * new should be written in it.
 */
export const EQUATION_OPERATORS: readonly string[] = [
  "==",
  "!=",
  ">",
  ">=",
  "<",
  "<=",
  "contains",
  "not_contains",
  "exists",
  "not_exist",
];

/**
 * The operators that test their left operand's presence and ignore `right`
 * entirely (`_UNARY_OPERATORS` in `flow.py`). The editor hides the `right`
 * input for these — showing one the worker never reads misleads the author.
 */
export const UNARY_EQUATION_OPERATORS: readonly string[] = ["exists", "not_exist"];

/**
 * A fresh `transition_condition` of *type*, in the shape the worker parses.
 *
 * `equation` seeds exactly one row rather than an empty array:
 * `evaluate_equation_condition` (`flow.py`) returns `False` for an empty
 * `equations` list, so a condition with none would silently never fire — the
 * editor must never produce that shape, even transiently after a type
 * switch.
 */
export function emptyCondition(type: "prompt" | "equation"): TransitionCondition {
  if (type === "prompt") return { type: "prompt", prompt: "" };
  return {
    type: "equation",
    operator: "&&",
    equations: [{ left: "", operator: "==", right: "" }],
  };
}

function isEdgeObject(value: unknown): value is FlowEdge {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Yield every edge a node carries, tagged with the shape it came from and
 * (for `edges[]` entries) its position in that list.
 *
 * Mirrors `worker/src/arhiteq_worker/flow.py`'s `iter_node_edges` exactly:
 * `edges[]` first in list order, then the four single-edge fields in
 * `EDGE_SHAPES` order. Non-object entries are skipped. `index` is the
 * position within `edges[]`, or `-1` for a single-edge shape, so callers
 * (`patchEdge`/`deleteEdge`) can address a specific edge for a later action.
 */
export function iterNodeEdges(
  node: FlowNode,
): { shape: EdgeShape; edge: FlowEdge; index: number }[] {
  const result: { shape: EdgeShape; edge: FlowEdge; index: number }[] = [];
  const edges = node.edges;
  if (Array.isArray(edges)) {
    edges.forEach((edge, index) => {
      if (isEdgeObject(edge)) result.push({ shape: "edges", edge, index });
    });
  }
  for (const shape of SINGLE_EDGE_SHAPES) {
    const edge = node[shape];
    if (isEdgeObject(edge)) result.push({ shape, edge, index: -1 });
  }
  return result;
}

/**
 * Which of the four single-edge shapes it makes sense to offer an "Add"
 * control for, per node type. Mirrors what the worker actually reads:
 *
 * - `flow.py:fallback_edge` looks for `else_edge` on exactly `branch`,
 *   `function` and `extract_dynamic_variables` (falling back to the single
 *   `edge` on `transfer_call`).
 * - `always_edge`/`skip_response_edge` are followed by the four node types
 *   whose handler is `flow_runtime.py`'s `_enter_conversation` — its
 *   `_handlers` map registers `conversation`, `subagent`, `function` AND
 *   `extract_dynamic_variables` against it, and its own docstring names all
 *   four. (`skip_response_edge` is read there; `always_edge` in
 *   `on_user_turn`, off whatever node is current — which is any of those
 *   four, since `branch`/`transfer_call` route away on entry and never stay
 *   current.) `function`/`extract_dynamic_variables` therefore take three
 *   addable shapes, not one: the editor used to offer them `else_edge`
 *   alone, so a graph the worker executes fine was not authorable here.
 *
 * Never lists `"edges"`: that shape is a list, unbounded, and already
 * creatable by dragging a connection on the canvas.
 */
const ADDABLE_SHAPES_BY_NODE_TYPE: Record<string, readonly EdgeShape[]> = {
  branch: ["else_edge"],
  function: ["else_edge", "always_edge", "skip_response_edge"],
  extract_dynamic_variables: ["else_edge", "always_edge", "skip_response_edge"],
  press_digit: ["else_edge", "always_edge", "skip_response_edge"],
  transfer_call: ["edge"],
  conversation: ["always_edge", "skip_response_edge"],
  subagent: ["always_edge", "skip_response_edge"],
};

/**
 * Node types that exist only to ROUTE — the worker's
 * `flow_runtime.py:_ROUTING_NODE_TYPES`, verbatim. `_dead_end` ENDS THE CALL
 * for these when a fallback is unusable, because they hold no conversation of
 * their own; `conversation`/`subagent` stay put instead (the model keeps
 * talking), which is why they are deliberately absent.
 */
const ROUTING_NODE_TYPES = new Set([
  "branch",
  "function",
  "extract_dynamic_variables",
  "transfer_call",
  "press_digit",
]);

/** The fallback shapes `flow.py:fallback_edge` consults, in its order. */
const FALLBACK_SHAPES = new Set<EdgeShape>(["else_edge", "edge"]);

export type DanglingNote = { tone: "error" | "info"; text: string };

/**
 * What a destination-less edge of *shape* on a node of *nodeType* actually
 * does at runtime — which is NOT "ends the call" for three of the five
 * shapes, and depends on the node type for the other two:
 *
 * - `else_edge`/`edge`: `flow.py:fallback_edge` returns `None` rather than
 *   the raw edge when it is dangling, so `_follow_fallback` goes to
 *   `_dead_end` — which ends the call only for `ROUTING_NODE_TYPES` and
 *   otherwise logs and stays put. This is the one genuinely fatal case.
 * - `edges[]`: a dangling entry is skipped, not fatal — `prompt_edges`
 *   ("Dangling authored edges … are excluded") never offers it to the model,
 *   and `select_equation_edge` continues past it. The node keeps every other
 *   option it has. The real `prior_auth_hotline.json` fixture ships one.
 * - `always_edge`: `on_user_turn` follows it only
 *   `if isinstance(always, dict) and always.get("destination_node_id")`.
 * - `skip_response_edge`: `_enter_conversation`'s auto-follow branch is
 *   likewise gated on `skip.get("destination_node_id")`, so the node just
 *   waits for the caller like any other.
 */
export function danglingEdgeNote(shape: EdgeShape, nodeType: string): DanglingNote {
  if (FALLBACK_SHAPES.has(shape)) {
    if (ROUTING_NODE_TYPES.has(nodeType)) {
      return {
        tone: "error",
        text:
          "No destination set. The worker treats a dangling fallback as unresolvable, and this " +
          "node type cannot hold the conversation — the call ends here.",
      };
    }
    return {
      tone: "info",
      text:
        "No destination set, so the worker ignores this fallback. This node type keeps the " +
        "conversation going instead of ending the call.",
    };
  }
  if (shape === "edges") {
    return {
      tone: "info",
      text:
        "No destination set, so the worker never offers this transition — it can never be " +
        "taken. Every other transition on this node still works.",
    };
  }
  return {
    tone: "info",
    text:
      "No destination set, so the worker never follows this edge — the node behaves as if it " +
      "were not there and waits for the caller.",
  };
}

/**
 * The single-edge shapes worth offering an "Add …" control for on *node*,
 * minus whichever it already has (each of the four can exist at most once
 * per node — `connect` replaces rather than appends for these shapes). A
 * freshly palette-added node can be missing a shape entirely (`defaultsFor`
 * gives a new `transfer_call` no `edge` field, a new `branch`/`function` no
 * `else_edge`) — without this, a user has no way to author that node's
 * guaranteed fallback, and an absent fallback dead-ends the call exactly like
 * a dangling one (`flow.py:fallback_edge`, `flow_runtime.py:_dead_end`).
 */
export function addableEdgeShapes(node: FlowNode): EdgeShape[] {
  const candidates = ADDABLE_SHAPES_BY_NODE_TYPE[node.type] ?? [];
  const present = new Set(iterNodeEdges(node).map((e) => e.shape));
  return candidates.filter((shape) => !present.has(shape));
}

// ---------------------------------------------------------------------------
// Condition summaries. ONE summariser, used by both readers of an edge's
// condition: the settings panel's collapsed row (`EdgeList`) and the canvas
// edge label (`flowGraph.labelFor`). They were two copies once, and the copies
// drifted the moment one of them learned that `exists` is unary — the panel
// stopped printing the stale `right` operand while the canvas kept printing
// it for the same edge. Anything either reader needs beyond this string
// (a fallback for "no condition") belongs at the call site, not in a fork.
// ---------------------------------------------------------------------------

/**
 * A one-line, human-readable rendering of *edge*'s transition condition, or
 * `null` when it has no readable condition (no condition object, an empty
 * prompt, an empty `equations` list, or a condition type this editor does not
 * model). Callers supply their own fallback for `null`.
 *
 * `exists` is unary — the worker's `_evaluate_single_equation` never reads
 * `right` for it, and `EquationBuilder` deliberately preserves a stale
 * `right` rather than clearing it on operator switch, so this is the one
 * place that must stop showing it.
 */
export function summarizeCondition(edge: FlowEdge): string | null {
  const condition = edge.transition_condition;
  if (!condition || typeof condition !== "object") return null;

  if (condition.type === "prompt") {
    return typeof condition.prompt === "string" && condition.prompt.trim() ? condition.prompt : null;
  }

  if (condition.type === "equation") {
    const equations = Array.isArray(condition.equations) ? condition.equations : [];
    if (equations.length === 0) return null;
    const joiner = condition.operator === "||" ? " || " : " && ";
    return equations
      .map((eq) => {
        const e = (eq ?? {}) as Record<string, unknown>;
        // A unary operator has no `right` to render — printing one would
        // append a stray empty operand to the edge label on the canvas.
        if (typeof e.operator === "string" && UNARY_EQUATION_OPERATORS.includes(e.operator)) {
          return `${e.left ?? ""} ${e.operator}`.trim();
        }
        return `${e.left ?? ""} ${e.operator ?? ""} ${e.right ?? ""}`.trim();
      })
      .join(joiner);
  }

  return null;
}

// ---------------------------------------------------------------------------
// Transfer destination validation.
// ---------------------------------------------------------------------------

// worker/src/arhiteq_worker/tools.py:E164_RE — kept in sync by hand since the
// worker's own module cannot be imported client-side.
const E164_RE = /^\+[1-9]\d{1,14}$/;

// A `{{name}}` placeholder anywhere in the value. Deliberately looser than
// `variables.py:_PLACEHOLDER` (which also models Retell's one level of
// nesting): this only decides whether the value is *resolved before* it is
// validated, and any `{{…}}` at all makes that true.
const PLACEHOLDER_RE = /\{\{[^{}]*\}\}/;

export type TransferNumberStatus = "empty" | "e164" | "template" | "invalid";

/**
 * How a `transfer_call` node's predefined `number` will fare at call time.
 *
 * The worker's `_transfer_number` (`flow_runtime.py`) runs `resolve_template`
 * on the authored value FIRST and only then matches `E164_RE`, so
 * `{{transfer_number}}` — or `+1{{extension}}` — is a legitimate authored
 * value whose validity is not knowable here: `"template"`, not `"invalid"`.
 * Calling it an error would red-flag a configuration that works.
 */
export function transferNumberStatus(value: string): TransferNumberStatus {
  const trimmed = value.trim();
  if (!trimmed) return "empty";
  if (E164_RE.test(trimmed)) return "e164";
  if (PLACEHOLDER_RE.test(trimmed)) return "template";
  return "invalid";
}

// ---------------------------------------------------------------------------
// Node instruction patches. One helper, shared by every editor that writes an
// `instruction`, because the `type` key is what makes the text audible at all.
// ---------------------------------------------------------------------------

/**
 * A node's `instruction` with *patch* applied, and its `type` guaranteed.
 *
 * Preserving an existing `type` is the point — a `prompt` line is phrased by
 * the model, a `static_text` one is spoken verbatim, and flipping it changes
 * what the caller hears. But a node that carries no `instruction` object at
 * all (any node authored outside this editor: `defaultsFor` seeds one, an
 * import need not) has no type to preserve, and a bare `{text: "…"}` is
 * silently INAUDIBLE: the worker's `static_text()` returns nothing unless
 * `type === "static_text"` and `node_instructions()` nothing unless
 * `type === "prompt"` (`worker/src/arhiteq_worker/flow.py`). The caller
 * supplies the type that its own UI is already displaying for the absent
 * case, so what is stored matches what the editor claims is stored.
 */
export function withInstruction(
  existing: unknown,
  patch: { text?: string; type?: string },
  defaultType: "prompt" | "static_text",
): Record<string, unknown> {
  const base =
    existing && typeof existing === "object" ? (existing as Record<string, unknown>) : {};
  const merged: Record<string, unknown> = { ...base, ...patch };
  if (typeof merged.type !== "string" || !merged.type) merged.type = defaultType;
  return merged;
}

/**
 * The comma-separated "Choices" field of an `extract_dynamic_variables` enum
 * parsed into `variable.choices`.
 *
 * Note what this destroys: the separator and the spaces around it. That makes
 * it unusable as the round trip for a controlled input — feeding
 * `choices.join(", ")` back into the box erases the comma the instant it is
 * typed, so a second choice can never be entered. The field keeps the raw
 * text the user is typing in local state and parses through here on the way
 * to the document only.
 */
export function parseChoices(raw: string): string[] {
  return raw
    .split(",")
    .map((choice) => choice.trim())
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// Id minting. Retell's own ids look like `node-<ms>` / `edge-<ms>-<rand>`;
// staying close to that convention keeps imported and authored graphs
// visually consistent (and avoids a second id "look" the user has to learn).
// ---------------------------------------------------------------------------

// Module-level counter, not `Math.random()`/`crypto.randomUUID()`: it is what
// makes many `newNodeId` calls within the same millisecond unique (a real
// concern — a user double-clicking the palette, or a test loop, can call
// this faster than `Date.now()`'s resolution).
let nodeIdCounter = 0;

/** A fresh node id: `node-<ms>-<counter>`. `type` is accepted for call-site clarity. */
export function newNodeId(_type: string): string {
  return `node-${Date.now()}-${nodeIdCounter++}`;
}

function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 11);
}

/**
 * A fresh edge id following the fixtures' convention: `edge-<ms>-<rand>` for
 * every shape except `skip_response_edge`, which gets its own
 * `skip-response-edge-<ms>-<rand>` prefix (both seen verbatim in
 * `backend/tests/fixtures/retell_flows/prior_auth_hotline.json`).
 */
export function newEdgeId(shape: EdgeShape): string {
  const prefix = shape === "skip_response_edge" ? "skip-response-edge" : "edge";
  return `${prefix}-${Date.now()}-${randomSuffix()}`;
}

let noteIdCounter = 0;

function newNoteId(): string {
  return `note-${Date.now()}-${noteIdCounter++}`;
}

let toolIdCounter = 0;

/**
 * A fresh flow tool id: `tool-<ms>-<counter>`. Real Retell tool ids look like
 * `tool-<ms>`; the counter suffix is the same collision-proofing `newNodeId`
 * uses above, needed here for the same reason — `stampToolIds` can mint
 * several of these in one `Array.map` pass, all within the same millisecond.
 */
export function newToolId(): string {
  return `tool-${Date.now()}-${toolIdCounter++}`;
}

/**
 * Stamps a `newToolId()` onto any flow tool missing one, leaving every other
 * tool untouched.
 *
 * `FunctionSettings.tsx`'s tool picker, and the worker's
 * `make_function_node_tool` (`worker/src/arhiteq_worker/flow.py`), both
 * resolve a `function` node's action by matching its `tool_id` against
 * `flow.tools[]` entries' own `tool_id` — a flow-scoped id unrelated to the
 * tool's `name`. `FunctionsSection` was built for the LLM-agent
 * `general_tools[]` shape, which has no such id, so a tool added through it
 * saves with none — the worker then silently skips wiring that node's action
 * on a live call. This is called from the page level (the one place that
 * knows it is writing to a flow's `tools[]` rather than an LLM's
 * `general_tools[]`); `FunctionsSection` itself stays agnostic of which
 * source it edits. Pure: returns a new array and never mutates *tools* or
 * its entries, so the fidelity rule (spread, don't rebuild) still holds for
 * every field this function doesn't touch.
 */
export function stampToolIds(tools: Record<string, unknown>[]): Record<string, unknown>[] {
  return tools.map((tool) =>
    typeof tool.tool_id === "string" && tool.tool_id ? tool : { ...tool, tool_id: newToolId() },
  );
}

// ---------------------------------------------------------------------------
// knownVariables
// ---------------------------------------------------------------------------

/**
 * Every `{{variable}}` name the flow already knows about, sorted and
 * de-duplicated, from three sources: the flow's own
 * `default_dynamic_variables` keys, every `extract_dynamic_variables` node's
 * `variables[].name`, and every tool's `response_variables` keys. Used to
 * populate autocomplete/validation for prompt and equation text later.
 */
export function knownVariables(flow: RawConversationFlow): string[] {
  const names = new Set<string>();

  const defaults = flow.default_dynamic_variables;
  if (defaults && typeof defaults === "object") {
    for (const key of Object.keys(defaults)) names.add(key);
  }

  const nodes = Array.isArray(flow.nodes) ? (flow.nodes as FlowNode[]) : [];
  for (const node of nodes) {
    if (node.type !== "extract_dynamic_variables") continue;
    const variables = node.variables;
    if (!Array.isArray(variables)) continue;
    for (const variable of variables) {
      if (
        variable &&
        typeof variable === "object" &&
        typeof (variable as Record<string, unknown>).name === "string"
      ) {
        names.add((variable as Record<string, unknown>).name as string);
      }
    }
  }

  const tools = Array.isArray(flow.tools) ? (flow.tools as Record<string, unknown>[]) : [];
  for (const tool of tools) {
    const responseVariables = tool.response_variables;
    if (responseVariables && typeof responseVariables === "object") {
      for (const key of Object.keys(responseVariables as Record<string, unknown>)) {
        names.add(key);
      }
    }
  }

  return [...names].sort();
}

// ---------------------------------------------------------------------------
// seedFlow — the graph a brand-new flow starts from.
// ---------------------------------------------------------------------------

/**
 * A minimal, runnable two-node graph for a brand-new flow: a `conversation`
 * start node speaking a static greeting, wired by one `edges[]` entry to an
 * `end` node. POSTed to `/create-conversation-flow`, it must load in the
 * worker without raising `FlowError` — every node type is supported and the
 * one edge names an existing destination, so there is no unsupported type
 * and no dead end.
 */
export function seedFlow(): Partial<RawConversationFlow> {
  const startId = newNodeId("conversation");
  const endId = newNodeId("end");

  const startNode: FlowNode = {
    id: startId,
    type: "conversation",
    name: "Start",
    display_position: { x: 200, y: 200 },
    instruction: { type: "static_text", text: "Hi! How can I help you today?" },
    edges: [
      {
        id: newEdgeId("edges"),
        destination_node_id: endId,
        transition_condition: { type: "prompt", prompt: "" },
      },
    ],
  };

  const endNode: FlowNode = {
    id: endId,
    type: "end",
    name: "End",
    display_position: { x: 550, y: 200 },
    instruction: { type: "static_text", text: "Goodbye." },
  };

  return {
    global_prompt: "",
    nodes: [startNode, endNode],
    start_node_id: startId,
    start_speaker: "agent",
    // The suggested tier in `lib/models.ts`, not the older 2.5 default this
    // used to seed: 2.5 is not in Retell's `LLMModel` enum at all (they list
    // gemini-3.0-flash / 3.1-flash-lite / 3.5-flash), so a flow created here
    // carried a model id no Retell consumer recognizes.
    model_choice: { type: "cascading", model: DEFAULT_FLOW_MODEL, high_priority: true },
  };
}

// ---------------------------------------------------------------------------
// The reducer. Every action deep-copies the flow (`structuredClone` — never
// `JSON.parse(JSON.stringify(...))`, which silently drops `undefined` values
// and mishandles `Date`), mutates the copy, and returns it. The input is
// never touched.
// ---------------------------------------------------------------------------

export type FlowAction =
  | { type: "patchFlow"; patch: Record<string, unknown> }
  | { type: "patchNode"; nodeId: string; patch: Record<string, unknown> }
  | { type: "addNode"; nodeType: string; position: Position }
  | { type: "moveNode"; nodeId: string; position: Position }
  | { type: "deleteNode"; nodeId: string }
  | { type: "connect"; nodeId: string; shape: EdgeShape; destinationNodeId: string }
  | {
      type: "patchEdge";
      nodeId: string;
      shape: EdgeShape;
      index: number;
      patch: Record<string, unknown>;
    }
  | { type: "deleteEdge"; nodeId: string; shape: EdgeShape; index: number }
  | { type: "setStartNode"; nodeId: string }
  | { type: "addNote"; position: Position; content?: string }
  | { type: "patchNote"; noteId: string; patch: Record<string, unknown> }
  | { type: "deleteNote"; noteId: string };

function assertNever(action: never): never {
  throw new Error(`unhandled flow action: ${JSON.stringify(action)}`);
}

function nodesOf(flow: RawConversationFlow): FlowNode[] {
  if (!Array.isArray(flow.nodes)) flow.nodes = [];
  return flow.nodes as FlowNode[];
}

function notesOf(flow: RawConversationFlow): Record<string, unknown>[] {
  if (!Array.isArray(flow.notes)) flow.notes = [];
  return flow.notes as Record<string, unknown>[];
}

function findNode(flow: RawConversationFlow, nodeId: string): FlowNode {
  const node = nodesOf(flow).find((n) => n.id === nodeId);
  if (!node) throw new Error(`no such node: ${nodeId}`);
  return node;
}

/**
 * The React Flow id `toReactFlow` mints for a note that carries no `id` of its
 * own. Real Retell notes have one; an import whose notes do not still has to
 * be addressable, and positionally is the only way.
 *
 * `::` matches the separator `flowGraph.ts` already uses for edge ids and does
 * not appear in Retell's own ids, so this cannot be confused for a node id —
 * and `noteIndexFor` bounds-checks it against the notes array anyway.
 */
export function syntheticNoteId(index: number): string {
  return `note::${index}`;
}

/**
 * Where *noteId* lives in `flow.notes`, or -1.
 *
 * The single place that resolves BOTH forms — a note's own `id` and the
 * synthetic positional one — so `flowGraph.ts`'s "is this a note or a node?"
 * routing and this module's `patchNote`/`deleteNote` can never disagree about
 * which ids are notes. They used to: `isNoteId` matched only real ids, so
 * dragging an id-less note routed to `moveNode`, and `findNode` threw from
 * inside a state updater and took the editor down.
 */
export function noteIndexFor(flow: RawConversationFlow, noteId: string): number {
  const notes = Array.isArray(flow.notes) ? (flow.notes as Record<string, unknown>[]) : [];
  const byId = notes.findIndex((note) => note.id === noteId);
  if (byId !== -1) return byId;
  // Only a note that has no id of its own answers to a synthetic one — a real
  // node whose id happened to look like `note::0` must not be captured here.
  const prefixed = noteId.startsWith("note::") ? Number(noteId.slice("note::".length)) : NaN;
  if (!Number.isInteger(prefixed) || prefixed < 0 || prefixed >= notes.length) return -1;
  return typeof notes[prefixed].id === "string" ? -1 : prefixed;
}

/** `{id, type, name, display_position, ...typeDefaults}` for a new node of *nodeType*. */
function defaultsFor(nodeType: string): Record<string, unknown> {
  switch (nodeType) {
    case "conversation":
    case "subagent":
      return { instruction: { type: "prompt", text: "" }, edges: [] };
    case "branch":
      return { edges: [] };
    case "function":
      return { edges: [], wait_for_result: true };
    case "end":
      return { instruction: { type: "static_text", text: "" } };
    case "transfer_call":
      return { transfer_destination: { type: "predefined", number: "" } };
    case "extract_dynamic_variables":
      return { variables: [], edges: [] };
    case "press_digit":
      // A `prompt` instruction, not `static_text`: the node tells the model
      // WHICH digits to press after listening to the menu — it carries no
      // literal digits of its own (`make_press_digit_node_tool`).
      return { instruction: { type: "prompt", text: "" }, edges: [] };
    default:
      return {};
  }
}

const NODE_TYPE_LABELS: Record<string, string> = {
  conversation: "Conversation",
  branch: "Branch",
  function: "Function",
  extract_dynamic_variables: "Extract Variables",
  press_digit: "Press Digit",
  transfer_call: "Transfer Call",
  subagent: "Subagent",
  end: "End",
};

export function flowReducer(flow: RawConversationFlow, action: FlowAction): RawConversationFlow {
  const next = structuredClone(flow);

  switch (action.type) {
    case "patchFlow": {
      Object.assign(next, action.patch);
      return next;
    }

    case "patchNode": {
      const node = findNode(next, action.nodeId);
      Object.assign(node, action.patch);
      return next;
    }

    case "addNode": {
      const id = newNodeId(action.nodeType);
      const node: FlowNode = {
        id,
        type: action.nodeType,
        name: NODE_TYPE_LABELS[action.nodeType] ?? action.nodeType,
        display_position: action.position,
        ...defaultsFor(action.nodeType),
      };
      nodesOf(next).push(node);
      return next;
    }

    case "moveNode": {
      const node = findNode(next, action.nodeId);
      node.display_position = action.position;
      return next;
    }

    case "deleteNode": {
      const { nodeId } = action;
      next.nodes = nodesOf(next).filter((n) => n.id !== nodeId);
      for (const node of nodesOf(next)) {
        const edges = node.edges;
        if (Array.isArray(edges)) {
          node.edges = edges.filter(
            (edge) => !(isEdgeObject(edge) && edge.destination_node_id === nodeId),
          );
        }
        for (const shape of SINGLE_EDGE_SHAPES) {
          const edge = node[shape];
          if (isEdgeObject(edge) && edge.destination_node_id === nodeId) {
            delete node[shape];
          }
        }
      }
      if (next.start_node_id === nodeId) {
        next.start_node_id = nodesOf(next)[0]?.id ?? "";
      }
      return next;
    }

    case "connect": {
      const node = findNode(next, action.nodeId);
      if (action.shape === "edges") {
        const edges = Array.isArray(node.edges) ? (node.edges as FlowEdge[]) : [];
        edges.push({
          id: newEdgeId("edges"),
          destination_node_id: action.destinationNodeId,
          transition_condition: { type: "prompt", prompt: "" },
        });
        node.edges = edges;
      } else {
        node[action.shape] = {
          id: newEdgeId(action.shape),
          destination_node_id: action.destinationNodeId,
          transition_condition: { type: "prompt", prompt: "" },
        } satisfies FlowEdge;
      }
      return next;
    }

    case "patchEdge": {
      const node = findNode(next, action.nodeId);
      if (action.shape === "edges") {
        const edges = Array.isArray(node.edges) ? (node.edges as FlowEdge[]) : [];
        const edge = edges[action.index];
        if (edge && isEdgeObject(edge)) Object.assign(edge, action.patch);
      } else {
        const edge = node[action.shape];
        if (isEdgeObject(edge)) Object.assign(edge, action.patch);
      }
      return next;
    }

    case "deleteEdge": {
      const node = findNode(next, action.nodeId);
      if (action.shape === "edges") {
        const edges = Array.isArray(node.edges) ? (node.edges as FlowEdge[]) : [];
        node.edges = edges.filter((_, index) => index !== action.index);
      } else {
        delete node[action.shape];
      }
      return next;
    }

    case "setStartNode": {
      next.start_node_id = action.nodeId;
      return next;
    }

    case "addNote": {
      const note = {
        id: newNoteId(),
        content: action.content ?? "",
        display_position: action.position,
      };
      notesOf(next).push(note);
      return next;
    }

    case "patchNote": {
      // Resolved by index, not by `find(n => n.id === ...)`: an id-less note
      // is addressed by the synthetic positional id (`noteIndexFor`).
      const index = noteIndexFor(next, action.noteId);
      if (index !== -1) Object.assign(notesOf(next)[index], action.patch);
      return next;
    }

    case "deleteNote": {
      const index = noteIndexFor(next, action.noteId);
      if (index !== -1) next.notes = notesOf(next).filter((_, i) => i !== index);
      return next;
    }

    default:
      return assertNever(action);
  }
}

// ---------------------------------------------------------------------------
// diffFlowPatch — the autosave PATCH payload, minus whatever didn't change.
// ---------------------------------------------------------------------------

/**
 * The subset of *next*'s top-level keys whose value actually differs from
 * *current*'s, for building an autosave PATCH payload that sends only what
 * changed rather than the whole flow on every keystroke.
 *
 * Compares by **value** (`JSON.stringify`), never by reference (`Object.is`):
 * `flowReducer` `structuredClone`s the entire flow on every action, so every
 * non-primitive top-level field (`nodes`, `components`, `kb_config`, …) gets a
 * fresh reference whether or not its content changed. An `Object.is` diff
 * therefore "wins" on nearly every key, in effect sending a near-full rewrite
 * on every 800ms autosave tick — do not "optimise" this back to reference
 * comparison; it cannot work while the reducer deep-clones. A flow is small,
 * so stringifying both sides here (at most once per debounce) is cheap.
 */
export function diffFlowPatch(
  current: RawConversationFlow,
  next: RawConversationFlow,
): Partial<RawConversationFlow> {
  const patch: Partial<RawConversationFlow> = {};
  for (const key of Object.keys(next)) {
    const before = (current as Record<string, unknown>)[key];
    const after = (next as Record<string, unknown>)[key];
    if (JSON.stringify(after) !== JSON.stringify(before)) {
      (patch as Record<string, unknown>)[key] = after;
    }
  }
  return patch;
}
