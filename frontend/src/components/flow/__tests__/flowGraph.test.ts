import { describe, expect, test } from "bun:test";
import type { NodeChange } from "@xyflow/react";
import { flowReducer, iterNodeEdges, type FlowNode } from "../flowModel";
import {
  edgeAddress,
  edgeRemovalActions,
  nodeChangeAction,
  reuseUnchanged,
  toReactFlow,
  type FlowEdgeData,
  type FlowNodeData,
} from "../flowGraph";
import type { RawConversationFlow } from "@/lib/api";
import { load, NAMES } from "./fixtures";

describe("toReactFlow", () => {
  test.each(NAMES.map((n) => [n] as const))("%s: every node becomes exactly one React Flow node", (name) => {
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
    // The real prior-auth fixture has exactly three dangling edges, one per
    // list-vs-single shape: an `else_edge` on a function node, an `edge` on a
    // transfer_call node, and an `edges[]` entry on a subagent node. React
    // Flow cannot draw an edge with no target, so all three are dropped from
    // the canvas (the settings panel still shows them, which is where they
    // get fixed).
    const flow = load("prior_auth_hotline.json");
    const { edges } = toReactFlow(flow);
    const ids = new Set((flow.nodes as FlowNode[]).map((n) => n.id));
    for (const e of edges) expect(ids.has(e.target)).toBe(true);
    // Pin the count, not just "no broken targets": a `toReactFlow` that
    // dropped every edge would satisfy the loop above vacuously.
    const authored = (flow.nodes as FlowNode[]).flatMap((n) => iterNodeEdges(n));
    const dangling = authored.filter((e) => !e.edge.destination_node_id);
    expect(dangling.length).toBe(3);
    expect(edges.length).toBe(authored.length - dangling.length);
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

  test("a unary exists is labelled without its stale right operand", () => {
    // The settings panel and the canvas summarise the SAME edge, and used to
    // do it with two copies of the logic: when the panel learned that
    // `exists` is unary (the worker's `_evaluate_single_equation` never reads
    // `right`, and `EquationBuilder` deliberately keeps a stale one), the
    // canvas kept printing `right` for the very same edge. One summariser
    // now, shared -- `flowModel.test.ts` pins the other end of it.
    const cond = {
      type: "equation",
      operator: "&&",
      equations: [{ left: "{{policy_id}}", operator: "exists", right: "42" }],
    };
    const flow = { conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [
        { id: "n1", type: "branch", edges: [
          { id: "e1", destination_node_id: "n2", transition_condition: cond }] },
        { id: "n2", type: "end" },
      ]} as unknown as RawConversationFlow;
    const { edges } = toReactFlow(flow);
    expect((edges[0].data as FlowEdgeData).label).toBe("{{policy_id}} exists");
  });

  test("an empty prompt condition falls back to the shape's own word", () => {
    // A freshly dragged connection carries `{type: "prompt", prompt: ""}`
    // until the user writes one; labelling it with that empty string left a
    // nameless connector on the canvas.
    const flow = { conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [
        { id: "n1", type: "conversation", edges: [
          { id: "e1", destination_node_id: "n2",
            transition_condition: { type: "prompt", prompt: "" } }] },
        { id: "n2", type: "end" },
      ]} as unknown as RawConversationFlow;
    const { edges } = toReactFlow(flow);
    expect((edges[0].data as FlowEdgeData).label).toBe("condition");
  });

  test("notes render as their own node type", () => {
    const flow = load("prior_auth_hotline.json");
    const { nodes } = toReactFlow(flow);
    expect(nodes.filter((n) => n.type === "note").length).toBe(
      (flow.notes as unknown[]).length,
    );
  });

  // The three fallbacks below are unreachable from the real fixtures, so
  // nothing else pins them. Task 4 renders all three, which is exactly why an
  // untested one would surface as a blank label or a note stacked at 0,0.
  test("an edges[] entry with an unrecognized condition type still gets a label", () => {
    const flow = {
      conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [
        { id: "n1", type: "conversation", edges: [
          // A condition type neither the editor nor the worker knows. Retell
          // could ship one; the canvas must not render a nameless connector.
          { id: "e1", destination_node_id: "n2",
            transition_condition: { type: "someday_new_kind" } }] },
        { id: "n2", type: "end" },
      ],
    } as unknown as RawConversationFlow;
    const { edges } = toReactFlow(flow);
    expect((edges[0].data as FlowEdgeData).label).toBe("condition");
  });

  test("a note with no display_position or size gets deterministic defaults", () => {
    const flow = {
      conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [{ id: "n1", type: "end" }],
      notes: [{ id: "note-1", content: "no position, no size" }],
    } as unknown as RawConversationFlow;
    const note = toReactFlow(flow).nodes.find((n) => n.type === "note")!;
    expect(note.position).toEqual(toReactFlow(flow).nodes.find((n) => n.type === "note")!.position);
    expect(typeof note.position.x).toBe("number");
    expect(Number.isFinite(note.position.x)).toBe(true);
    expect(note.style).toMatchObject({ width: 220, height: 120 });
  });

  test("a note keeps its authored size when it has one", () => {
    const flow = {
      conversation_flow_id: "f", version: 0, start_node_id: "n1",
      nodes: [{ id: "n1", type: "end" }],
      notes: [{ id: "note-1", content: "sized", size: { width: 345, height: 118 } }],
    } as unknown as RawConversationFlow;
    const note = toReactFlow(flow).nodes.find((n) => n.type === "note")!;
    expect(note.style).toMatchObject({ width: 345, height: 118 });
  });
});

describe("nodeChangeAction", () => {
  // The real prior-auth fixture ships two real notes (`note-1775763167442`,
  // `note-1775763195463`) alongside real graph nodes — using it (rather than
  // synthetic notes) is what proves these ids are reachable from an actual
  // Retell capture, not just a shape the code happens to accept.
  const flow = load("prior_auth_hotline.json");
  const noteId = "note-1775763167442";
  const graphNodeId = flow.start_node_id as string;

  test("dragging a note to rest produces patchNote with the new display_position", () => {
    const change: NodeChange = {
      type: "position",
      id: noteId,
      position: { x: 111, y: 222 },
      dragging: false,
    };
    expect(nodeChangeAction(change, flow)).toEqual({
      type: "patchNote",
      noteId,
      patch: { display_position: { x: 111, y: 222 } },
    });
  });

  test("deleting a note produces deleteNote", () => {
    const change: NodeChange = { type: "remove", id: noteId };
    expect(nodeChangeAction(change, flow)).toEqual({ type: "deleteNote", noteId });
  });

  test("dragging a graph node to rest still produces moveNode", () => {
    const change: NodeChange = {
      type: "position",
      id: graphNodeId,
      position: { x: 50, y: 60 },
      dragging: false,
    };
    expect(nodeChangeAction(change, flow)).toEqual({
      type: "moveNode",
      nodeId: graphNodeId,
      position: { x: 50, y: 60 },
    });
  });

  test("deleting a graph node still produces deleteNode", () => {
    const change: NodeChange = { type: "remove", id: graphNodeId };
    expect(nodeChangeAction(change, flow)).toEqual({ type: "deleteNode", nodeId: graphNodeId });
  });

  test("a mid-drag position change (dragging: true) produces null", () => {
    const change: NodeChange = {
      type: "position",
      id: noteId,
      position: { x: 1, y: 2 },
      dragging: true,
    };
    expect(nodeChangeAction(change, flow)).toBeNull();
  });

  test("a selection change produces null", () => {
    const change: NodeChange = { type: "select", id: graphNodeId, selected: true };
    expect(nodeChangeAction(change, flow)).toBeNull();
  });
});

describe("edgeRemovalActions", () => {
  test("a batch is applied high index first, so no address is invalidated", () => {
    const actions = edgeRemovalActions([
      "n1::edges::0",
      "n1::edges::2",
      "n1::edges::1",
    ]);
    expect(actions.map((a) => (a as { index: number }).index)).toEqual([2, 1, 0]);
  });

  test("deleting a node keeps every OTHER transition of a node that reached it twice", () => {
    // The regression this whole function exists for. React Flow's
    // `deleteElements` fires every edge of a removed node in ONE
    // `onEdgesChange` batch; `edges[]` addresses are positional and
    // `deleteEdge` splices. Applying such a batch in arrival order removes
    // index 0, shifts the rest left, then removes what is NOW index 2 — a
    // still-wanted transition — while the edge actually addressed survives.
    const flow = {
      conversation_flow_id: "f",
      version: 0,
      start_node_id: "n1",
      nodes: [
        {
          id: "n1",
          type: "branch",
          edges: [
            { id: "e0", destination_node_id: "doomed" },
            { id: "e1", destination_node_id: "keep_c" },
            { id: "e2", destination_node_id: "doomed" },
            { id: "e3", destination_node_id: "keep_d" },
          ],
        },
        { id: "doomed", type: "end" },
        { id: "keep_c", type: "end" },
        { id: "keep_d", type: "end" },
      ],
    } as unknown as RawConversationFlow;

    // Exactly what React Flow hands `onEdgesChange` when `doomed` is deleted:
    // both edges pointing at it, addressed by their position at render time.
    const batch = ["n1::edges::0", "n1::edges::2"];
    const after = edgeRemovalActions(batch).reduce(flowReducer, flow);

    const survivors = ((after.nodes as FlowNode[])[0].edges as { id: string }[]).map((e) => e.id);
    expect(survivors).toEqual(["e1", "e3"]);
  });

  test("single-edge shapes are deleted by key, so batch order cannot corrupt them", () => {
    const actions = edgeRemovalActions(["n1::else_edge::-1", "n1::edges::0"]);
    expect(actions).toEqual([
      { type: "deleteEdge", nodeId: "n1", shape: "edges", index: 0 },
      { type: "deleteEdge", nodeId: "n1", shape: "else_edge", index: -1 },
    ]);
  });
});

describe("reuseUnchanged", () => {
  test("an unchanged entry comes back as the very same object", () => {
    // Identity is the whole point: React Flow drops a node's measured size
    // the moment it is handed a different object, and a node with no measured
    // size renders `visibility: hidden`.
    const previous = [{ id: "a", data: { n: 1 } }];
    const next = [{ id: "a", data: { n: 1 } }];
    expect(reuseUnchanged(previous, next)[0]).toBe(previous[0]);
  });

  test("a changed entry is NOT reused", () => {
    const previous = [{ id: "a", data: { n: 1 } }];
    const next = [{ id: "a", data: { n: 2 } }];
    const result = reuseUnchanged(previous, next);
    expect(result[0]).toBe(next[0]);
    expect(result[0].data.n).toBe(2);
  });

  test("editing one node leaves every other node's object untouched", () => {
    // The realistic case: `flowReducer` structuredClones the document, so one
    // keystroke replaces the objects of ALL nodes, not just the edited one.
    const flow = {
      conversation_flow_id: "f",
      version: 0,
      start_node_id: "n1",
      nodes: [
        { id: "n1", type: "conversation", instruction: { type: "prompt", text: "hi" } },
        { id: "n2", type: "conversation", instruction: { type: "prompt", text: "there" } },
        { id: "n3", type: "end" },
      ],
    } as unknown as RawConversationFlow;

    const before = toReactFlow(flow).nodes;
    const edited = flowReducer(flow, {
      type: "patchNode",
      nodeId: "n1",
      patch: { instruction: { type: "prompt", text: "hi!" } },
    });
    const after = reuseUnchanged(before, toReactFlow(edited).nodes);

    expect(after[0]).not.toBe(before[0]); // n1 really changed
    expect(after[1]).toBe(before[1]);
    expect(after[2]).toBe(before[2]);
  });

  test("added and removed ids are handled without reuse", () => {
    const previous = [{ id: "a" }, { id: "gone" }];
    const next = [{ id: "a" }, { id: "new" }];
    const result = reuseUnchanged(previous, next);
    expect(result[0]).toBe(previous[0]);
    expect(result[1]).toBe(next[1]);
    expect(result.map((r) => r.id)).toEqual(["a", "new"]);
  });
});
