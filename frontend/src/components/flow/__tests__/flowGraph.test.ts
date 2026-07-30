import { describe, expect, test } from "bun:test";
import type { FlowNode } from "../flowModel";
import { edgeAddress, toReactFlow, type FlowEdgeData, type FlowNodeData } from "../flowGraph";
import type { RawConversationFlow } from "@/lib/api";
import { load, NAMES } from "./fixtures";

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
