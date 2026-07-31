/**
 * The pure adapter between a Retell conversation flow and React Flow's
 * `{ nodes, edges }` shape. This module never renders anything — it only
 * maps `flowModel.ts`'s types onto plain data React Flow (Task 4's canvas)
 * consumes. It must not import or render a component; only types (`Node`,
 * `Edge`) and the `MarkerType` value come from `@xyflow/react`.
 *
 * FIDELITY: nodes carry the *original* `FlowNode` object in `data.node`
 * unchanged (never a rebuilt/typed copy) — the canvas and the node
 * inspector read and patch through that reference, so an unknown key
 * survives the trip to the canvas and back out through the reducer.
 *
 * Edge addressing: an authored edge id (`edge-1`, say) repeats across many
 * nodes in real Retell flows, but React Flow requires globally unique edge
 * ids. `edgeAddress`/the id encoding here bridge that: the React Flow edge
 * id is `${nodeId}::${shape}::${index}`, where `index` is `iterNodeEdges`'s
 * position within `edges[]` (or `-1` for the four single-edge shapes) —
 * exactly what `patchEdge`/`deleteEdge` in `flowModel.ts` need to address a
 * specific edge.
 */

import type { CSSProperties } from "react";
import type { Edge as RFEdge, Node as RFNode, NodeChange } from "@xyflow/react";
import { MarkerType } from "@xyflow/react";
import {
  iterNodeEdges,
  noteIndexFor,
  summarizeCondition,
  syntheticNoteId,
  type EdgeShape,
  type FlowAction,
  type FlowEdge,
  type FlowNode,
  type Position,
} from "./flowModel";
import type { RawConversationFlow } from "@/lib/api";

// ---------------------------------------------------------------------------
// Node/edge data shapes React Flow carries alongside its own id/position.
// ---------------------------------------------------------------------------

export type FlowNodeData = {
  node: FlowNode;
  isStart: boolean;
  isGlobal: boolean;
};

export type FlowEdgeData = {
  nodeId: string;
  shape: EdgeShape;
  index: number;
  label: string;
};

export type NoteNodeData = {
  note: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// Edge id encoding/decoding.
// ---------------------------------------------------------------------------

function encodeEdgeId(nodeId: string, shape: EdgeShape, index: number): string {
  return `${nodeId}::${shape}::${index}`;
}

/** Parses a React Flow edge id minted by `toReactFlow` back into its address. */
export function edgeAddress(rfEdgeId: string): { nodeId: string; shape: EdgeShape; index: number } {
  const parts = rfEdgeId.split("::");
  const indexPart = parts.pop() ?? "-1";
  const shape = (parts.pop() ?? "edges") as EdgeShape;
  const nodeId = parts.join("::");
  return { nodeId, shape, index: Number(indexPart) };
}

// ---------------------------------------------------------------------------
// React Flow NodeChange -> FlowAction. The canvas's `nodes` array is a union
// of two entity kinds (`toReactFlow` appends note nodes after graph nodes),
// but `flowModel.ts` keeps them as two separate action families
// (`moveNode`/`deleteNode` vs. `patchNote`/`deleteNote`) addressed by two
// separate id spaces (`flow.nodes` vs. `flow.notes`). This is the one place
// that tells a note id from a node id and routes to the right action, so the
// canvas component never has to. Pure and DOM-free on purpose: it is the only
// seam this plan's "no rendering tests" rule leaves for a regression test.
// ---------------------------------------------------------------------------

function isNoteId(flow: RawConversationFlow, id: string): boolean {
  // Delegated to `flowModel`'s resolver so this routing and the note actions
  // it routes to agree on exactly which ids are notes — including the
  // synthetic positional id `toReactFlow` mints for a note with no `id`.
  return noteIndexFor(flow, id) !== -1;
}

/**
 * Maps one React Flow `NodeChange` onto the `FlowAction` it should dispatch,
 * or `null` if the change must not mutate the flow: a `select`/`dimensions`
 * change (editor-local, not flow content), or a mid-drag `position` change
 * (`dragging: true` fires on every pixel — only the drag-end change, with
 * `dragging: false`, should reach the reducer).
 */
export function nodeChangeAction(change: NodeChange, flow: RawConversationFlow): FlowAction | null {
  if (change.type === "position") {
    if (change.dragging !== false || !change.position) return null;
    return isNoteId(flow, change.id)
      ? { type: "patchNote", noteId: change.id, patch: { display_position: change.position } }
      : { type: "moveNode", nodeId: change.id, position: change.position };
  }

  if (change.type === "remove") {
    return isNoteId(flow, change.id)
      ? { type: "deleteNote", noteId: change.id }
      : { type: "deleteNode", nodeId: change.id };
  }

  return null;
}

// ---------------------------------------------------------------------------
// React Flow EdgeChange -> FlowAction. Same seam as `nodeChangeAction` above,
// and for a sharper reason: edge removals arrive in BATCHES whose addresses
// are positional, so the order they are applied in changes which edges die.
// ---------------------------------------------------------------------------

/**
 * Maps a batch of removed React Flow edge ids onto the `deleteEdge` actions
 * that remove exactly those edges — **in an order that stays correct as the
 * list shrinks under them.**
 *
 * React Flow deletes in batches: `deleteElements` collects every edge
 * connected to a removed node and fires them all in ONE `onEdgesChange` call
 * (`matchingEdges.map(elementToRemoveChange)` then a single
 * `triggerEdgeChanges`). An `edges[]` address is POSITIONAL, and
 * `flowModel`'s `deleteEdge` splices by that position, so applying a batch in
 * arrival order corrupts it: a branch node with `edges = [→B, →C, →B, →D]`
 * deleting node B yields `remove ::edges::0` and `remove ::edges::2`, and
 * applying them in that order drops index 0, shifts every later edge left,
 * then drops what is NOW index 2 — the still-wanted `→D` transition, while
 * the second `→B` survives.
 *
 * Descending index order fixes it: every address in the batch still points at
 * what it pointed at when the batch was minted, because only entries *after*
 * it have been removed so far. The four single-edge shapes carry index `-1`
 * and are deleted by key, so their position in the order is irrelevant.
 */
export function edgeRemovalActions(rfEdgeIds: readonly string[]): FlowAction[] {
  return rfEdgeIds
    .map(edgeAddress)
    .sort((a, b) => b.index - a.index)
    .map(({ nodeId, shape, index }) => ({ type: "deleteEdge", nodeId, shape, index }) as const);
}

// ---------------------------------------------------------------------------
// Referential identity across renders.
// ---------------------------------------------------------------------------

/**
 * Returns `next` with every element swapped for the PREVIOUS render's element
 * of the same id whenever the two are value-identical.
 *
 * This preserves object identity, which React Flow treats as load-bearing.
 * `adoptUserNodes` keeps a node's internal record only while
 * `userNode === internals.userNode`; on any other object it takes the rebuild
 * branch, which re-derives `measured` from `userNode.measured` and resets
 * `handleBounds` via `parseHandles`. Nothing here sets `measured`/`width`/
 * `initialWidth` on a graph node, so a rebuild leaves every node with no
 * dimensions — `nodeHasDimensions` goes false and `NodeWrapper` renders
 * `visibility: hidden` until the ResizeObserver re-measures it a frame later.
 *
 * That would otherwise happen constantly, because two upstream layers destroy
 * identity wholesale: `flowReducer` `structuredClone`s the document on every
 * action, so one keystroke in one node's instruction replaces the objects of
 * ALL of them, and `toReactFlow` mints fresh wrappers on every call. The
 * result is the whole graph blinking on each edit. Comparing by value here
 * confines the rebuild to the nodes that actually changed.
 *
 * Value comparison is by `JSON.stringify`, which is sound for these two
 * arrays specifically: everything in them is JSON data (the flow document
 * plus booleans, numbers and style strings), there are no functions or
 * cycles, and `structuredClone` preserves key order — so a clone of an
 * unchanged node stringifies identically to its original.
 */
export function reuseUnchanged<T extends { id: string }>(
  previous: readonly T[],
  next: readonly T[],
): T[] {
  if (previous.length === 0) return [...next];
  const byId = new Map<string, T>();
  for (const item of previous) byId.set(item.id, item);
  return next.map((item) => {
    const prior = byId.get(item.id);
    return prior !== undefined && JSON.stringify(prior) === JSON.stringify(item) ? prior : item;
  });
}

// ---------------------------------------------------------------------------
// Deterministic layout fallback for nodes/notes Retell never positioned.
// ---------------------------------------------------------------------------

function isPosition(value: unknown): value is Position {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).x === "number" &&
    typeof (value as Record<string, unknown>).y === "number"
  );
}

/** A stable grid cell for the *index*-th item that carries no authored position. */
function fallbackPosition(index: number): Position {
  return { x: 40 + (index % 4) * 320, y: 40 + Math.floor(index / 4) * 220 };
}

function positionOf(displayPosition: unknown, index: number): Position {
  return isPosition(displayPosition) ? displayPosition : fallbackPosition(index);
}

// ---------------------------------------------------------------------------
// Global-node detection — mirrors the worker's `FlowGraph.global_nodes` test.
// ---------------------------------------------------------------------------

function isGlobalNode(node: FlowNode): boolean {
  const setting = node.global_node_setting;
  if (!setting || typeof setting !== "object") return false;
  return Boolean((setting as Record<string, unknown>).condition);
}

// ---------------------------------------------------------------------------
// Edge labels: the condition text when there is one, else the shape's own
// word for a runtime edge that fires unconditionally.
// ---------------------------------------------------------------------------

const SHAPE_LABEL: Record<EdgeShape, string> = {
  edges: "condition",
  else_edge: "else",
  edge: "failed",
  always_edge: "always",
  skip_response_edge: "skip response",
};

/**
 * The condition text, or the shape's own word when there is none to show.
 * `summarizeCondition` is shared with `EdgeList`'s collapsed row on purpose —
 * a second copy here is exactly how the canvas came to keep printing the
 * stale `right` operand of a unary `exists` after the panel stopped.
 */
function labelFor(shape: EdgeShape, edge: FlowEdge): string {
  return summarizeCondition(edge) ?? SHAPE_LABEL[shape];
}

// ---------------------------------------------------------------------------
// Edge styling by shape — five different runtime meanings, five different
// looks, so the canvas never collapses into one connector style.
// ---------------------------------------------------------------------------

type EdgeVisual = {
  style: CSSProperties;
  animated: boolean;
  markerEnd?: RFEdge["markerEnd"];
};

const EDGE_VISUAL: Record<EdgeShape, EdgeVisual> = {
  edges: {
    style: { stroke: "var(--color-ink)", strokeWidth: 1.5 },
    animated: false,
  },
  else_edge: {
    style: { stroke: "var(--color-sub)", strokeWidth: 1.5, strokeDasharray: "6 4" },
    animated: false,
  },
  edge: {
    // Dash pattern differs from `else_edge`'s "6 4" on purpose. Both are
    // dashed fallbacks and colour alone ("muted" vs "bad") is the one
    // difference a red/green-deficient reader may not see at all — and these
    // two mean genuinely different things: `else_edge` is "nothing matched",
    // `edge` is "the transfer failed".
    style: { stroke: "var(--color-bad)", strokeWidth: 1.5, strokeDasharray: "2 3" },
    animated: false,
  },
  always_edge: {
    style: { stroke: "var(--color-accent)", strokeWidth: 1.5 },
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--color-accent)" },
  },
  skip_response_edge: {
    style: { stroke: "var(--color-faint)", strokeWidth: 1.5, strokeDasharray: "1 4" },
    animated: false,
  },
};

// ---------------------------------------------------------------------------
// toReactFlow
// ---------------------------------------------------------------------------

const DEFAULT_NOTE_WIDTH = 220;
const DEFAULT_NOTE_HEIGHT = 120;

function noteSize(note: Record<string, unknown>): { width: number; height: number } {
  const size = note.size;
  const width =
    size && typeof size === "object" && typeof (size as Record<string, unknown>).width === "number"
      ? ((size as Record<string, unknown>).width as number)
      : DEFAULT_NOTE_WIDTH;
  const height =
    size && typeof size === "object" && typeof (size as Record<string, unknown>).height === "number"
      ? ((size as Record<string, unknown>).height as number)
      : DEFAULT_NOTE_HEIGHT;
  return { width, height };
}

/** Turns a Retell conversation flow into React Flow's `{ nodes, edges }`. */
export function toReactFlow(flow: RawConversationFlow): { nodes: RFNode[]; edges: RFEdge[] } {
  const flowNodes = Array.isArray(flow.nodes) ? (flow.nodes as FlowNode[]) : [];
  const nodeIds = new Set(flowNodes.map((n) => n.id));

  const graphNodes: RFNode[] = flowNodes.map((node, index) => ({
    id: node.id,
    type: "flowNode",
    position: positionOf(node.display_position, index),
    data: {
      node,
      isStart: node.id === flow.start_node_id,
      isGlobal: isGlobalNode(node),
    } satisfies FlowNodeData,
  }));

  const rawNotes = Array.isArray(flow.notes) ? (flow.notes as Record<string, unknown>[]) : [];
  const noteNodes: RFNode[] = rawNotes.map((note, index) => {
    const { width, height } = noteSize(note);
    const id = typeof note.id === "string" ? note.id : syntheticNoteId(index);
    return {
      id,
      type: "note",
      position: positionOf(note.display_position, index),
      data: { note } satisfies NoteNodeData,
      style: { width, height },
    };
  });

  const edges: RFEdge[] = [];
  for (const node of flowNodes) {
    for (const { shape, edge, index } of iterNodeEdges(node)) {
      const destination = edge.destination_node_id;
      if (typeof destination !== "string" || !nodeIds.has(destination)) continue;

      const visual = EDGE_VISUAL[shape];
      edges.push({
        id: encodeEdgeId(node.id, shape, index),
        source: node.id,
        target: destination,
        style: visual.style,
        animated: visual.animated,
        markerEnd: visual.markerEnd,
        data: {
          nodeId: node.id,
          shape,
          index,
          label: labelFor(shape, edge),
        } satisfies FlowEdgeData,
      });
    }
  }

  return { nodes: [...graphNodes, ...noteNodes], edges };
}
