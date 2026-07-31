/**
 * Auto layout: a left-to-right layered placement for the flow graph, pure and
 * DOM-free so it can be unit-tested (the canvas itself is not).
 *
 * Layering is by longest path from the start node, not by first visit: a node
 * reachable in one hop *and* in four must sit in the fourth column, or its
 * incoming edge would point backwards and the picture stops reading as a
 * left-to-right conversation.
 *
 * Cycles ("didn't catch that — ask again") are ordinary in these flows, and
 * longest path is unbounded on a cyclic graph, so the back edges are dropped
 * first: one DFS from the start node classifies every edge whose destination
 * is still on the stack as a back edge and excludes it, leaving a DAG whose
 * longest paths are what the columns are then computed from. Merely capping
 * the relaxation instead — which is what this did first — let a node on a loop
 * drift one column further right per sweep, so a flow with a re-ask loop laid
 * itself out as a diagonal smear with its edges pointing every which way.
 *
 * Nodes with no path from the start node (orphans an author has dragged out
 * but not wired yet, and every node when `start_node_id` names nothing) are
 * kept and placed in a trailing column of their own rather than dropped or
 * stacked on the origin.
 */

import { iterNodeEdges, type FlowNode, type Position } from "./flowModel";
import type { RawConversationFlow } from "@/lib/api";

/**
 * Column pitch and row pitch. `NodeShell` fixes a node card at 260px wide, so
 * 360 leaves a 100px gutter for the edge labels; card height varies with the
 * subtitle, so the row pitch is generous rather than measured.
 */
const COLUMN_GAP = 360;
const ROW_GAP = 180;
const ORIGIN: Position = { x: 80, y: 80 };

/** Every node id *node* has an outgoing edge to, in edge order, deduplicated. */
function destinationsOf(node: FlowNode, known: ReadonlySet<string>): string[] {
  const seen = new Set<string>();
  for (const { edge } of iterNodeEdges(node)) {
    const destination = edge.destination_node_id;
    if (typeof destination === "string" && known.has(destination)) seen.add(destination);
  }
  return [...seen];
}

/**
 * The reachable subgraph as an adjacency map with every back edge removed —
 * i.e. the DAG the columns are computed over. Keys are exactly the nodes
 * reachable from *startId*.
 */
function forwardAdjacency(
  byId: ReadonlyMap<string, FlowNode>,
  ids: ReadonlySet<string>,
  startId: string,
): Map<string, string[]> {
  const adjacency = new Map<string, string[]>();
  const onStack = new Set<string>();
  const stack: { id: string; next: number; destinations: string[] }[] = [];

  const enter = (id: string) => {
    const node = byId.get(id);
    adjacency.set(id, []);
    onStack.add(id);
    stack.push({ id, next: 0, destinations: node ? destinationsOf(node, ids) : [] });
  };

  enter(startId);
  while (stack.length > 0) {
    const frame = stack[stack.length - 1];
    if (frame.next >= frame.destinations.length) {
      onStack.delete(frame.id);
      stack.pop();
      continue;
    }
    const destination = frame.destinations[frame.next];
    frame.next += 1;
    // Still on the stack ⇒ this edge closes a loop (a self-loop included).
    // Keeping it would make "longest path" meaningless.
    if (onStack.has(destination)) continue;
    adjacency.get(frame.id)?.push(destination);
    if (!adjacency.has(destination)) enter(destination);
  }
  return adjacency;
}

/**
 * New `display_position`s for every node in *flow*, keyed by node id — the
 * payload of a `moveNodes` action. Returns an empty object for a flow with no
 * nodes, which the caller can treat as "nothing to lay out".
 *
 * Notes are deliberately untouched: they are the author's own annotations,
 * pinned where they were put, and moving them is not what "auto layout" means.
 */
export function autoLayout(flow: RawConversationFlow): Record<string, Position> {
  const nodes = Array.isArray(flow.nodes) ? (flow.nodes as FlowNode[]) : [];
  if (nodes.length === 0) return {};

  const ids = new Set(nodes.map((node) => node.id));
  const byId = new Map(nodes.map((node) => [node.id, node] as const));
  const startId = typeof flow.start_node_id === "string" ? flow.start_node_id : "";

  // ------------------------------------------------------------ layering
  const layer = new Map<string, number>();
  if (ids.has(startId)) {
    const adjacency = forwardAdjacency(byId, ids, startId);

    // Longest path over that DAG, relaxed in topological order (Kahn): a node
    // is only placed once every edge into it has been accounted for, which is
    // exactly the "past the longest path in" rule.
    const indegree = new Map<string, number>();
    for (const id of adjacency.keys()) indegree.set(id, 0);
    for (const destinations of adjacency.values()) {
      for (const destination of destinations) {
        indegree.set(destination, (indegree.get(destination) ?? 0) + 1);
      }
    }

    const queue = [...indegree].flatMap(([id, degree]) => (degree === 0 ? [id] : []));
    for (const id of queue) layer.set(id, 0);
    for (let head = 0; head < queue.length; head += 1) {
      const id = queue[head];
      const depth = (layer.get(id) ?? 0) + 1;
      for (const destination of adjacency.get(id) ?? []) {
        if ((layer.get(destination) ?? -1) < depth) layer.set(destination, depth);
        const remaining = (indegree.get(destination) ?? 0) - 1;
        indegree.set(destination, remaining);
        if (remaining === 0) queue.push(destination);
      }
    }
  }

  // Unreachable nodes share one column past the deepest reachable one.
  const deepest = layer.size > 0 ? Math.max(...layer.values()) : -1;
  for (const node of nodes) {
    if (!layer.has(node.id)) layer.set(node.id, deepest + 1);
  }

  // ------------------------------------------------------------- placing
  // Within a column, keep the flow's own node order: it is stable across runs
  // (so laying out twice does not reshuffle the picture) and roughly matches
  // creation order, which is the closest thing to the author's intent we have.
  const columns = new Map<number, string[]>();
  for (const node of nodes) {
    const column = layer.get(node.id) ?? 0;
    const members = columns.get(column);
    if (members) members.push(node.id);
    else columns.set(column, [node.id]);
  }

  // Centre each column vertically against the tallest one, so a fan-out reads
  // as a fan rather than as everything hanging off the top edge.
  const tallest = Math.max(...[...columns.values()].map((members) => members.length));

  const positions: Record<string, Position> = {};
  for (const [column, members] of columns) {
    const offset = ((tallest - members.length) * ROW_GAP) / 2;
    members.forEach((id, row) => {
      positions[id] = {
        x: ORIGIN.x + column * COLUMN_GAP,
        y: ORIGIN.y + offset + row * ROW_GAP,
      };
    });
  }
  return positions;
}
