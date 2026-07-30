import { describe, expect, test } from "bun:test";
import type { NodeChange } from "@xyflow/react";
import { iterNodeEdges, type FlowNode } from "../flowModel";
import { edgeAddress, nodeChangeAction, toReactFlow, type FlowEdgeData, type FlowNodeData } from "../flowGraph";
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
