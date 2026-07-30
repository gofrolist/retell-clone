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
// if the editor drifts from what Retell actually sends, these fail. Task 3
// lifts these three lines into `__tests__/fixtures.ts` when a second suite
// needs them; leave them inline here for now.
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
    // NOTE: not `.at(-1)` — clara_outbound.json's real Welcome Node (nodes[0])
    // already carries an `always_edge`, which sorts after `edges[]` in
    // iterNodeEdges' worker-matching order. `.at(-1)` would pick that
    // pre-existing edge instead of the one `connect` just appended. Find the
    // added edge by its destination instead of assuming position.
    const added = iterNodeEdges((next.nodes as FlowNode[])[0]).find(
      (e) => e.shape === "edges" && e.edge.destination_node_id === b.id,
    )!;
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
