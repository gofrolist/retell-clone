import { describe, expect, test } from "bun:test";
import { autoLayout } from "../autoLayout";
import { iterNodeEdges, type FlowNode } from "../flowModel";
import type { RawConversationFlow } from "@/lib/api";
import { load, NAMES } from "./fixtures";

const flowOf = (nodes: unknown[], startNodeId = "a"): RawConversationFlow =>
  ({
    conversation_flow_id: "f",
    version: 0,
    start_node_id: startNodeId,
    nodes,
  }) as unknown as RawConversationFlow;

const edgeTo = (destination: string) => ({
  id: `e-${destination}`,
  destination_node_id: destination,
  transition_condition: { type: "prompt", prompt: "" },
});

describe("autoLayout", () => {
  test("places the start node first and each hop in a further column", () => {
    const positions = autoLayout(
      flowOf([
        { id: "a", type: "conversation", edges: [edgeTo("b")] },
        { id: "b", type: "conversation", edges: [edgeTo("c")] },
        { id: "c", type: "end" },
      ]),
    );
    expect(positions.a.x).toBeLessThan(positions.b.x);
    expect(positions.b.x).toBeLessThan(positions.c.x);
  });

  test("a node reachable by two paths sits past the LONGEST one", () => {
    // a → b → c → d and a → d. Placing `d` by first visit would put it in
    // column 1, next to `b`, with its edge from `c` pointing backwards.
    const positions = autoLayout(
      flowOf([
        { id: "a", type: "conversation", edges: [edgeTo("b"), edgeTo("d")] },
        { id: "b", type: "conversation", edges: [edgeTo("c")] },
        { id: "c", type: "conversation", edges: [edgeTo("d")] },
        { id: "d", type: "end" },
      ]),
    );
    expect(positions.d.x).toBeGreaterThan(positions.c.x);
  });

  test("terminates on a cycle and keeps every node", () => {
    const positions = autoLayout(
      flowOf([
        { id: "a", type: "conversation", edges: [edgeTo("b")] },
        { id: "b", type: "conversation", edges: [edgeTo("c")] },
        // The "didn't catch that, ask again" loop every real flow has.
        { id: "c", type: "conversation", edges: [edgeTo("b"), edgeTo("d")] },
        { id: "d", type: "end" },
      ]),
    );
    expect(Object.keys(positions).sort()).toEqual(["a", "b", "c", "d"]);
    // …and in four columns, one per node. The regression this guards: with the
    // loop counted as a path, `b` and `c` were relaxed against each other once
    // per sweep and drifted right until the sweep cap stopped them.
    const columns = [...new Set(Object.values(positions).map((p) => p.x))].sort((x, y) => x - y);
    expect(columns.length).toBe(4);
    expect(columns.indexOf(positions.d.x)).toBe(3);
  });

  test("unwired nodes are placed too, past the reachable ones", () => {
    const positions = autoLayout(
      flowOf([
        { id: "a", type: "conversation", edges: [edgeTo("b")] },
        { id: "b", type: "end" },
        { id: "orphan", type: "conversation" },
      ]),
    );
    expect(positions.orphan).toBeDefined();
    expect(positions.orphan.x).toBeGreaterThan(positions.b.x);
  });

  test("a start_node_id naming nothing still lays every node out", () => {
    const positions = autoLayout(
      flowOf([{ id: "a", type: "conversation" }, { id: "b", type: "end" }], "missing"),
    );
    expect(Object.keys(positions).sort()).toEqual(["a", "b"]);
    // Distinct spots — the failure this guards is everything stacked on 0,0.
    expect(positions.a).not.toEqual(positions.b);
  });

  test("an empty flow yields nothing to move", () => {
    expect(autoLayout(flowOf([]))).toEqual({});
  });

  test.each(NAMES.map((n) => [n] as const))(
    "%s: every node is positioned exactly once, and no two share a spot",
    (name) => {
      const flow = load(name);
      const positions = autoLayout(flow);
      const nodes = flow.nodes as FlowNode[];
      expect(Object.keys(positions).sort()).toEqual(nodes.map((n) => n.id).sort());
      const spots = Object.values(positions).map((p) => `${p.x}:${p.y}`);
      expect(new Set(spots).size).toBe(spots.length);
    },
  );

  test.each(NAMES.map((n) => [n] as const))(
    "%s: every edge points forwards or sideways, never backwards except on a cycle",
    (name) => {
      const flow = load(name);
      const positions = autoLayout(flow);
      // A backwards edge is legitimate only when the destination can also
      // reach the source (a loop). Anything else is a layering bug.
      const nodes = flow.nodes as FlowNode[];
      const byId = new Map(nodes.map((n) => [n.id, n] as const));
      const reaches = (from: string, to: string): boolean => {
        const seen = new Set<string>();
        const stack = [from];
        while (stack.length) {
          const id = stack.pop() as string;
          if (id === to) return true;
          if (seen.has(id)) continue;
          seen.add(id);
          const node = byId.get(id);
          if (!node) continue;
          for (const { edge } of iterNodeEdges(node)) {
            const destination = edge.destination_node_id;
            if (typeof destination === "string") stack.push(destination);
          }
        }
        return false;
      };
      const startId = String(flow.start_node_id);
      for (const node of nodes) {
        // An unreachable node (a global node entered by condition rather than
        // by edge, or one an author left unwired) sits in the trailing column
        // by design, so its edges back into the graph are expected to point
        // left. The layering claim is about the reachable subgraph.
        if (!reaches(startId, node.id)) continue;
        for (const { edge } of iterNodeEdges(node)) {
          const destination = edge.destination_node_id;
          if (typeof destination !== "string" || !positions[destination]) continue;
          if (positions[destination].x >= positions[node.id].x) continue;
          expect(reaches(destination, node.id)).toBe(true);
        }
      }
    },
  );
});
