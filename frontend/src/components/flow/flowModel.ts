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
 * seven types as its `SUPPORTED_NODE_TYPES` — an eighth type in the palette
 * would produce a graph the worker rejects at call start.
 */

import type { RawConversationFlow } from "@/lib/api";

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
 * The seven node types the worker knows how to execute
 * (`SUPPORTED_NODE_TYPES` in `flow.py`), in palette display order. An eighth
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
  "end",
];

/**
 * Every `equation` comparison operator the worker actually implements
 * (`worker/src/arhiteq_worker/flow.py`'s `_EQUALITY_OPERATORS` |
 * `_NUMERIC_OPERATORS` | `_CONTAINMENT_OPERATORS`, plus the unary `exists`).
 * An operator offered here that the worker does not recognize produces an
 * edge whose condition silently evaluates to `False` forever
 * (`_evaluate_single_equation`'s "unrecognized operator" fallthrough) — this
 * list must never drift ahead of that module's.
 */
export const EQUATION_OPERATORS: readonly string[] = [
  "==",
  "!=",
  ">",
  "<",
  "CONTAINS",
  "NOT CONTAINS",
  "exists",
];

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
    model_choice: { type: "cascading", model: "gemini-2.5-flash", high_priority: true },
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
    default:
      return {};
  }
}

const NODE_TYPE_LABELS: Record<string, string> = {
  conversation: "Conversation",
  branch: "Branch",
  function: "Function",
  extract_dynamic_variables: "Extract Variables",
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
      const note = notesOf(next).find((n) => n.id === action.noteId);
      if (note) Object.assign(note, action.patch);
      return next;
    }

    case "deleteNote": {
      next.notes = notesOf(next).filter((n) => n.id !== action.noteId);
      return next;
    }

    default:
      return assertNever(action);
  }
}
