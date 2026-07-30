import { describe, expect, test } from "bun:test";
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
import { load, NAMES } from "./fixtures";

describe("fidelity", () => {
  test.each(NAMES.map((n) => [n] as const))("%s survives a no-op edit byte for byte", (name) => {
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

  test.each(NAMES.map((n) => [n] as const))("%s keeps keys the editor does not model", (name) => {
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
    // `start_node_id` is `string | null | undefined` on the wire, so assert it
    // is actually a string before the membership check — otherwise a reducer
    // that dropped it to null/undefined would fail on a confusing `toContain`
    // message rather than saying what went wrong.
    expect(typeof next.start_node_id).toBe("string");
    expect(ids).toContain(next.start_node_id as string);
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
    expect(added).toBeDefined();
    expect(added.edge.destination_node_id).toBe(b.id);
    expect(added.edge.transition_condition).toEqual({ type: "prompt", prompt: "" });
    expect(typeof added.edge.id).toBe("string");
    // Exactly one edge gained, in the shape asked for: catches a `connect`
    // that appends twice, or that writes a single-edge shape as well.
    expect(iterNodeEdges((next.nodes as FlowNode[])[0]).length).toBe(iterNodeEdges(a).length + 1);
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

describe("notes", () => {
  // The canvas renders `notes[]` as stickies and persists their positions, so
  // these three actions round-trip real authored content. The prior-auth
  // fixture carries real notes, which is what makes these non-synthetic.
  test("addNote appends to a flow that already has notes", () => {
    const flow = load("prior_auth_hotline.json");
    const before = (flow.notes as { id: string }[]).length;
    expect(before).toBeGreaterThan(0);
    const next = flowReducer(flow, { type: "addNote", position: { x: 3, y: 4 }, content: "hi" });
    const notes = next.notes as Record<string, unknown>[];
    expect(notes.length).toBe(before + 1);
    expect(notes[notes.length - 1]).toMatchObject({
      content: "hi",
      display_position: { x: 3, y: 4 },
    });
    expect(new Set(notes.map((n) => n.id)).size).toBe(notes.length);
  });

  test("addNote works on a flow with no notes key at all", () => {
    const flow = load("clara_outbound.json");
    const next = flowReducer(flow, { type: "addNote", position: { x: 0, y: 0 } });
    expect((next.notes as unknown[]).length).toBe(((flow.notes as unknown[]) ?? []).length + 1);
  });

  test("patchNote edits one note and leaves its siblings and unknown keys alone", () => {
    const flow = load("prior_auth_hotline.json");
    const [first, ...rest] = flow.notes as { id: string }[];
    const next = flowReducer(flow, {
      type: "patchNote",
      noteId: first.id,
      patch: { content: "rewritten" },
    });
    const notes = next.notes as Record<string, unknown>[];
    expect(notes[0].content).toBe("rewritten");
    // `size` is a real key on the fixture's notes that the editor never models.
    expect(notes[0].size).toEqual((first as Record<string, unknown>).size);
    expect(notes.slice(1)).toEqual(rest as unknown as Record<string, unknown>[]);
  });

  test("deleteNote removes only the named note", () => {
    const flow = load("prior_auth_hotline.json");
    const notes = flow.notes as { id: string }[];
    const next = flowReducer(flow, { type: "deleteNote", noteId: notes[0].id });
    const ids = (next.notes as { id: string }[]).map((n) => n.id);
    expect(ids).not.toContain(notes[0].id);
    expect(ids.length).toBe(notes.length - 1);
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
