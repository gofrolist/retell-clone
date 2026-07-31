/* eslint-disable react-hooks/refs --
 * Remembering the previous render's output is this hook's entire purpose, and
 * `react-hooks/refs` forbids exactly that. The exception is isolated in this
 * file rather than disabled at a call site so there is one place to delete if
 * the underlying need goes away.
 *
 * Why the need is real: React Flow keeps a node's measured size only while the
 * object it was given is identity-equal to the one it adopted
 * (`adoptUserNodes`' `userNode === internals.userNode` check). Any other
 * object takes the rebuild branch, which leaves the node with no dimensions —
 * and `NodeWrapper` renders a dimensionless node `visibility: hidden` until a
 * ResizeObserver pass restores it. Derived-from-a-document nodes are new
 * objects every time, so without this the whole graph blinks on every edit.
 *
 * Why the ref is safe here despite the rule: the value cached is never an
 * input to rendering — it is the previous render's own output, and
 * `reuseUnchanged` only ever substitutes an object that is VALUE-identical to
 * the one it replaces. A stale or discarded-render cache therefore cannot
 * produce a wrong graph; the worst case is a missed substitution, which is
 * exactly the behaviour we have without the hook at all.
 */
"use client";

import { useMemo, useRef } from "react";
import type { Edge as RFEdge, Node as RFNode } from "@xyflow/react";
import { reuseUnchanged } from "./flowGraph";

export type RenderedGraph = { nodes: RFNode[]; edges: RFEdge[] };

/**
 * Returns *graph* with every node and edge that did not change swapped back
 * to the object the previous render used. Pass a memoized `graph` — this hook
 * re-derives only when that object changes.
 */
export function useStableGraph(graph: RenderedGraph): RenderedGraph {
  const previous = useRef<RenderedGraph>({ nodes: [], edges: [] });

  const stable = useMemo(
    () => ({
      nodes: reuseUnchanged(previous.current.nodes, graph.nodes),
      edges: reuseUnchanged(previous.current.edges, graph.edges),
    }),
    [graph],
  );

  previous.current = stable;
  return stable;
}
