"use client";

// External stylesheets are allowed anywhere in the app directory, including
// colocated client components — see `node_modules/next/dist/docs/01-app/
// 01-getting-started/11-css.md`'s "External stylesheets" section.
import "@xyflow/react/dist/style.css";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type DragEvent,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeTypes,
  type NodeChange,
  type NodeTypes,
} from "@xyflow/react";
import type { RawConversationFlow } from "@/lib/api";
import { PillTabs } from "@/components/ui/Tabs";
import { cn } from "@/lib/utils";
import { LayoutGrid, Redo2, Undo2 } from "lucide-react";
import {
  edgeAddress,
  edgeRemovalActions,
  nodeChangeAction,
  toReactFlow,
  type FlowEdgeData,
} from "./flowGraph";
import { useStableGraph } from "./useStableGraph";
import { autoLayout } from "./autoLayout";
import {
  connectShapeFor,
  type FlowAction,
  type FlowNode as RetellNode,
  type Position,
} from "./flowModel";
import NodePalette, { NODE_DRAG_MIME } from "./NodePalette";
import FlowNode from "./nodes/FlowNode";
import NoteNode from "./nodes/NoteNode";
import GlobalSettings from "./settings/GlobalSettings";
import NodeSettings from "./settings/NodeSettings";

// Module-level constants: a fresh object literal each render makes React
// Flow remount every node/edge and log a warning.
const nodeTypes: NodeTypes = { flowNode: FlowNode, note: NoteNode };
const edgeTypes: EdgeTypes = {};

/** Sizes React Flow has measured, by node id — see `Canvas`'s `sizes` state. */
type Sizes = Record<string, { width: number; height: number }>;

/** The `type` of the flow node *nodeId* names, or `""` if it names none. */
function nodeTypeOf(flow: RawConversationFlow, nodeId: string): string {
  const nodes = Array.isArray(flow.nodes) ? (flow.nodes as RetellNode[]) : [];
  return nodes.find((node) => node.id === nodeId)?.type ?? "";
}

function ToolbarButton({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: typeof Undo2;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-ink transition-colors",
        disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer hover:bg-app",
      )}
    >
      <Icon className="size-4 text-sub" />
      {label}
    </button>
  );
}

function Canvas({
  flow,
  dispatch,
  readOnly,
  selectedNodeId,
  setSelectedNodeId,
  selectedEdgeId,
  setSelectedEdgeId,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
  selectedNodeId: string | null;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  selectedEdgeId: string | null;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}) {
  const { screenToFlowPosition, fitView } = useReactFlow();

  /**
   * Where a node is being dragged to *right now*. React Flow is controlled
   * here (`nodes` is a prop), so it does not move a node itself: it reports
   * the pointer's position through `onNodesChange` and renders whatever comes
   * back. Committing every one of those frames through `dispatch` would push
   * a graph rewrite (and an autosave draft entry) per pixel, so mid-drag
   * positions live here and only the drag-end position reaches the reducer.
   * Without this the dragged node stayed nailed to its old spot until the
   * mouse came up, which is the "the block doesn't move when I drag it" bug.
   */
  const [dragPositions, setDragPositions] = useState<Record<string, Position>>({});

  /**
   * Sizes React Flow measured, fed back into the nodes we hand it.
   *
   * React Flow keeps a node's measurements only while the object it adopted is
   * identity-equal to the one it is given (`adoptUserNodes`); any other object
   * takes the rebuild branch, which re-reads `measured` from OUR node — and a
   * node derived fresh from the flow document has none, so the rebuilt node is
   * dimensionless and `NodeWrapper` renders it `visibility: hidden` until the
   * ResizeObserver fires again. `useStableGraph` spares every *unchanged* node
   * that fate, but the node being dragged changes on every frame by
   * definition: without this it would blink out for the whole drag.
   */
  const [sizes, setSizes] = useState<Sizes>({});

  // The flow object is the single source of truth: `nodes`/`edges` are always
  // DERIVED from it, never a second source of truth held in local state.
  // Every mutation leaves through `dispatch`.
  //
  // Deriving them mints new objects, and React Flow reads object identity to
  // decide whether it may keep a node's measured size — hence
  // `useStableGraph`, without which the whole graph goes `visibility: hidden`
  // for a frame on every edit.
  const derived = useMemo(() => {
    const graph = toReactFlow(flow);
    return {
      nodes: graph.nodes.map((node) => {
        const measured = sizes[node.id];
        return {
          ...node,
          ...(measured ? { measured } : null),
          selected: node.id === selectedNodeId,
        };
      }),
      edges: graph.edges.map((edge) => {
        const selected = edge.id === selectedEdgeId;
        return {
          ...edge,
          selected,
          label: (edge.data as FlowEdgeData | undefined)?.label,
          // React Flow marks a selected edge with a CSS class, but every
          // edge here carries an inline `stroke` that outranks it. Widen
          // the stroke instead of recolouring it: the five shapes already
          // use colour to mean five different things, and width is the one
          // channel still free (and legible without colour vision).
          style: selected ? { ...edge.style, strokeWidth: 3 } : edge.style,
        };
      }),
    };
  }, [flow, selectedNodeId, selectedEdgeId, sizes]);

  // The in-flight drag is applied in a second pass, deliberately kept out of
  // the memo above: `toReactFlow` walks the whole document and
  // `useStableGraph` compares what comes out node by node, and a drag runs
  // that at pointer rate. This pass reuses every node object it does not move,
  // so a drag frame costs one new object and a row of reference comparisons
  // rather than a re-derivation of the graph.
  const graph = useMemo(() => {
    if (Object.keys(dragPositions).length === 0) return derived;
    return {
      nodes: derived.nodes.map((node) => {
        const position = dragPositions[node.id];
        return position ? { ...node, position } : node;
      }),
      edges: derived.edges,
    };
  }, [derived, dragPositions]);
  const { nodes, edges } = useStableGraph(graph);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const moves: Record<string, Position> = {};
      const settled: string[] = [];

      for (const change of changes) {
        if (change.type === "select") {
          if (change.selected) {
            setSelectedNodeId(change.id);
          } else {
            setSelectedNodeId((current) => (current === change.id ? null : current));
          }
          continue;
        }
        if (change.type === "dimensions" && change.dimensions) {
          const { width, height } = change.dimensions;
          setSizes((current) => {
            const known = current[change.id];
            if (known && known.width === width && known.height === height) return current;
            return { ...current, [change.id]: { width, height } };
          });
          continue;
        }
        if (readOnly) continue;
        if (change.type === "position") {
          // Mid-drag: hold it locally (see `dragPositions`). Drag-end still
          // falls through to `nodeChangeAction`, which is what commits it.
          if (change.dragging && change.position) {
            moves[change.id] = change.position;
            continue;
          }
          if (change.dragging === false) settled.push(change.id);
        }
        // `nodeChangeAction` tells a note id from a graph node id and
        // dispatches the right action family (`moveNode`/`deleteNode` vs.
        // `patchNote`/`deleteNote`); it also filters out changes that must
        // not mutate the flow (dimensions, mid-drag position changes).
        const action = nodeChangeAction(change, flow);
        if (!action) continue;
        dispatch(action);
        if (change.type === "remove") {
          setSelectedNodeId((current) => (current === change.id ? null : current));
        }
      }

      if (Object.keys(moves).length > 0) {
        setDragPositions((current) => ({ ...current, ...moves }));
      }
      // Dropped: the reducer now owns this position, so the local override has
      // to go — leaving it would pin the node here and silently outrank every
      // later `display_position` the document gets (an undo, say).
      if (settled.length > 0) {
        setDragPositions((current) => {
          if (!settled.some((id) => id in current)) return current;
          const next = { ...current };
          for (const id of settled) delete next[id];
          return next;
        });
      }
    },
    [dispatch, flow, readOnly, setSelectedNodeId],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      // Selection is editor-local, never flow content, so it is tracked even
      // on a read-only (older-version) canvas — and it has to be tracked at
      // all for an edge to be selectable: with `hasDefaultEdges: false` the
      // store never mutates the `edges` we pass, so `selected` reaching a
      // rendered edge, the Delete key finding one, and the selection ring
      // appearing are all downstream of this handler.
      for (const change of changes) {
        if (change.type !== "select") continue;
        if (change.selected) {
          setSelectedEdgeId(change.id);
        } else {
          setSelectedEdgeId((current) => (current === change.id ? null : current));
        }
      }

      if (readOnly) return;
      const removed = changes.flatMap((change) => (change.type === "remove" ? [change.id] : []));
      if (removed.length === 0) return;
      // Batched, and positional: the order these are applied in decides which
      // edges survive. See `edgeRemovalActions`.
      for (const action of edgeRemovalActions(removed)) dispatch(action);
      setSelectedEdgeId((current) =>
        current !== null && removed.includes(current) ? null : current,
      );
    },
    [dispatch, readOnly, setSelectedEdgeId],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly || !connection.source || !connection.target) return;
      // The shape depends on the SOURCE node's type, not on the gesture: a
      // transfer node's only outgoing edge is its `edge` failure fallback, and
      // an `end` node has none at all. Writing every drag into `edges[]` drew
      // connectors the worker never reads. `NodeShell` already hides the
      // handle where this is null, so that branch is belt and braces.
      const shape = connectShapeFor(nodeTypeOf(flow, connection.source));
      if (shape === null) return;
      dispatch({
        type: "connect",
        nodeId: connection.source,
        shape,
        destinationNodeId: connection.target,
      });
    },
    [dispatch, flow, readOnly],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      if (readOnly) return;
      const nodeType = event.dataTransfer.getData(NODE_DRAG_MIME);
      if (!nodeType) return;
      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      dispatch({ type: "addNode", nodeType, position });
    },
    [dispatch, readOnly, screenToFlowPosition],
  );

  const onDragOver = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (readOnly) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    },
    [readOnly],
  );

  // The graph has just moved wholesale, so frame it — but not from the click
  // handler: `fitView` reads React Flow's store, which is only updated once
  // the moved `flow` has come back down as a prop and been adopted. Fitting
  // inline (even one `requestAnimationFrame` later) framed the OLD positions
  // and left the freshly laid-out graph half off-screen.
  const [fitPending, setFitPending] = useState(false);
  useEffect(() => {
    if (!fitPending) return;
    // The flag is cleared inside the frame, not before it: clearing it here
    // re-runs this effect (its own dependency changed), whose cleanup then
    // cancels the frame it just scheduled — so the fit never happened.
    let cancelled = false;
    // Two frames, not one: the first gets us past this commit, the second past
    // React Flow's own store update (`adoptUserNodes` runs from its effects,
    // and `fitView` reads the node lookup that produces). Fitting after a
    // single frame framed the PREVIOUS positions — verified in the browser.
    let inner = 0;
    const frame = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => {
        if (!cancelled) {
          // Graph nodes only. Notes keep their authored spot (auto layout does
          // not move an author's annotations), and a real flow has one parked
          // thousands of pixels off to the side — fitting to it would zoom the
          // freshly tidied graph down to a smear.
          void fitView({
            duration: 300,
            padding: 0.2,
            minZoom: 0.05,
            nodes: (Array.isArray(flow.nodes) ? (flow.nodes as RetellNode[]) : []).map((node) => ({
              id: node.id,
            })),
          });
        }
        setFitPending(false);
      });
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      cancelAnimationFrame(inner);
    };
  }, [fitPending, fitView, flow, nodes]);

  const runAutoLayout = useCallback(() => {
    if (readOnly) return;
    const positions = autoLayout(flow);
    if (Object.keys(positions).length === 0) return;
    dispatch({ type: "moveNodes", positions });
    setFitPending(true);
  }, [dispatch, flow, readOnly]);

  return (
    <div className="h-full min-w-0 flex-1 bg-app" onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable
        deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
        // A real flow is thousands of pixels wide once laid out; React Flow's
        // default floor of 0.5 leaves "fit view" unable to actually fit it.
        minZoom={0.05}
        fitView
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls />
        <MiniMap pannable zoomable />
        <Panel position="top-right">
          <div className="flex items-center gap-0.5 rounded-lg border border-line bg-card p-1 shadow-sm">
            <ToolbarButton
              icon={Undo2}
              label="Undo"
              onClick={onUndo}
              disabled={readOnly || !canUndo}
            />
            <ToolbarButton
              icon={Redo2}
              label="Redo"
              onClick={onRedo}
              disabled={readOnly || !canRedo}
            />
            <span className="mx-0.5 h-5 w-px bg-line" aria-hidden />
            <ToolbarButton
              icon={LayoutGrid}
              label="Auto layout"
              onClick={runAutoLayout}
              disabled={readOnly}
            />
          </div>
        </Panel>
      </ReactFlow>
    </div>
  );
}

/** Actions whose repeats collapse into one undo step while they keep coming. */
function coalesceKey(action: FlowAction): string {
  switch (action.type) {
    case "patchFlow":
      return `patchFlow:${Object.keys(action.patch).join(",")}`;
    case "patchNode":
      return `patchNode:${action.nodeId}:${Object.keys(action.patch).join(",")}`;
    case "patchNote":
      return `patchNote:${action.noteId}:${Object.keys(action.patch).join(",")}`;
    case "patchEdge":
      return `patchEdge:${action.nodeId}:${action.shape}:${action.index}:${Object.keys(action.patch).join(",")}`;
    default:
      // Structural edits (add/delete/connect/move) each get their own step.
      return "";
  }
}

/** How long a run of same-target edits keeps folding into one undo step. */
const COALESCE_MS = 600;

/**
 * Undo/redo state. The coalescing bookkeeping (`lastKey`/`lastAt`) rides along
 * with the stacks rather than sitting in a ref, so `record` is a plain
 * function of state that can be handed to the settings sections the page
 * renders — a ref-reading callback cannot be, since nothing can prove it is
 * not called during their render.
 */
type History = {
  past: RawConversationFlow[];
  future: RawConversationFlow[];
  /** `coalesceKey` of the last recorded action, and when it was recorded. */
  lastKey: string;
  lastAt: number;
};

const EMPTY_HISTORY: History = { past: [], future: [], lastKey: "", lastAt: 0 };
/** Snapshots kept. Deep documents, so this is a memory bound, not a UX one. */
const HISTORY_LIMIT = 50;

/**
 * The three-pane conversation-flow canvas: node palette (left) · React Flow
 * canvas (center) · settings pane (right: `<GlobalSettings>` and whatever the
 * page hangs off it, or `<NodeSettings>` for the selection). `FlowEditor`
 * owns only editor-local state — selection, undo history, in-flight drags —
 * and every graph mutation goes out through `dispatch`, with `nodes`/`edges`
 * always derived from `flow` (see `Canvas` above), never held as a second
 * source of truth.
 *
 * `canvasEpoch` is a remount key for the canvas only, and the caller must
 * increment it ONLY on a bulk graph replacement — the whole graph swapped out
 * from under the editor: viewing a different published version, restoring a
 * version as a new draft, or any other reload of the whole document
 * (`page.tsx`'s `selectVersion`/`reload`).
 *
 * Why it exists: after such a replacement, React and the DOM update correctly
 * (browser-verified at the time via the accessibility tree and a live DOM
 * query — the new text really was there), but Chromium's compositor kept
 * painting the pre-update pixels for the affected node(s) indefinitely; no
 * re-render, resize or wait cleared it, only a forced reflow. Changing a
 * `key` is the standard way to force one: it unmounts and remounts every DOM
 * node in the canvas, so there is nothing stale left to paint over.
 *
 * Why it must not be `agent.version`, which it used to be: that number also
 * bumps on the FIRST EDIT of any published agent (`versions.touch` bumps it
 * when `is_published`, and a brand-new agent is published at V0), and
 * `page.tsx`'s flow save re-reads the agent — so a user typing into a new
 * agent's start node had the canvas remount and the settings pane unmount
 * their textarea mid-keystroke, 800ms after they started. An epoch the page
 * bumps explicitly at the two replacement sites cannot do that.
 *
 * Scope, stated plainly so it is not overclaimed: this remount addresses only
 * the post-replacement stale paint described above. Stale paint reported
 * against ordinary incremental edits (drag-wiring an edge, switching a
 * condition's type) was never reproduced and is NOT addressed here — those
 * edits deliberately do not change the epoch, precisely so they keep their
 * viewport and selection.
 */
export default function FlowEditor({
  flow,
  dispatch,
  readOnly,
  canvasEpoch,
  agentDetails,
  globalHeader,
  globalSections,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
  canvasEpoch: number;
  /** Cost/latency/token card, pinned under the node rail. */
  agentDetails?: ReactNode;
  /** Agent-level controls (voice, language) above the flow's own settings. */
  globalHeader?: ReactNode;
  /**
   * The settings accordions, below the flow's own settings.
   *
   * A function of `dispatch` rather than a node, because several of those
   * sections (functions, knowledge base, MCPs) patch the FLOW — the same
   * document undo restores wholesale. Dispatching around this editor would
   * leave those edits unsnapshotted, so the next undo would revert them along
   * with whatever it was aimed at. Taking the dispatch from here makes that
   * impossible to get wrong from the outside.
   */
  globalSections?: (dispatch: (action: FlowAction) => void) => ReactNode;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"node" | "global">("global");

  /**
   * Undo/redo over whole-document snapshots rather than inverse actions: the
   * reducer's actions are not invertible on their own (a `deleteNode` also
   * drops every edge pointing at it), and a flow is small enough that keeping
   * the documents is cheaper than getting inversion right. Snapshots are safe
   * to hold by reference because `flowReducer` clones before it mutates.
   */
  const [history, setHistory] = useState<History>(EMPTY_HISTORY);

  /**
   * `dispatch`, plus a snapshot of the document as it was before the action.
   * Everything the editor's own UI dispatches goes through this — including
   * the sections the page hands us, which is why they take their dispatch from
   * here. Undo and redo dispatch straight through `dispatch` so they don't
   * record themselves.
   */
  const record = useCallback(
    (action: FlowAction) => {
      const key = coalesceKey(action);
      const now = Date.now();
      setHistory((h) => {
        // A run of edits to the same field folds into one step…
        const fold = key !== "" && key === h.lastKey && now - h.lastAt < COALESCE_MS;
        // …and so does a batch, which is a different case: one user gesture
        // can dispatch several actions — deleting a node fires a `deleteEdge`
        // per edge into it *and* the `deleteNode`, all against the same
        // `flow`, since the prop cannot change within the tick. A snapshot per
        // action would cost one real undo plus N presses that restore a
        // document already on screen.
        const push = !fold && h.past.at(-1) !== flow;
        return {
          past: push ? [...h.past, flow].slice(-HISTORY_LIMIT) : h.past,
          // Still a new edit either way — anything redone from here is stale.
          future: [],
          lastKey: key,
          lastAt: now,
        };
      });
      dispatch(action);
    },
    [dispatch, flow],
  );

  const undo = useCallback(() => {
    if (readOnly) return;
    const previous = history.past.at(-1);
    if (!previous) return;
    setHistory({
      past: history.past.slice(0, -1),
      future: [flow, ...history.future],
      // Whatever the user was typing into, the next edit starts a fresh step.
      lastKey: "",
      lastAt: 0,
    });
    dispatch({ type: "setFlow", flow: previous });
  }, [dispatch, flow, history, readOnly]);

  const redo = useCallback(() => {
    if (readOnly) return;
    const next = history.future[0];
    if (!next) return;
    setHistory({
      past: [...history.past, flow],
      future: history.future.slice(1),
      lastKey: "",
      lastAt: 0,
    });
    dispatch({ type: "setFlow", flow: next });
  }, [dispatch, flow, history, readOnly]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      const isUndo = key === "z" && !event.shiftKey;
      const isRedo = (key === "z" && event.shiftKey) || key === "y";
      if (!isUndo && !isRedo) return;
      // Nothing to undo on a published version, and swallowing the key to do
      // nothing is worse than leaving it to the browser.
      if (readOnly) return;
      const target = event.target as HTMLElement | null;
      // A text field has its own undo stack, and taking ⌘Z away from a user
      // mid-sentence to revert a graph edit they can't see is worse than not
      // having the shortcut at all. The same argument covers a dialog: while
      // one is open the graph is behind it, so a keystroke aimed at the dialog
      // must not quietly rewrite what it is covering.
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
          target.closest('[role="dialog"]') !== null)
      ) {
        return;
      }
      event.preventDefault();
      if (isRedo) redo();
      else undo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [readOnly, redo, undo]);

  // React Flow's selection is single-entity across kinds: selecting an edge
  // deselects every node (`addSelectedEdges` fires `triggerNodeChanges` with
  // a deselect for the whole node lookup), so `selectedNodeId` is null for as
  // long as an edge is selected. Falling back to the edge's SOURCE node keeps
  // the pane useful, because that node's `EdgeList` row is exactly where the
  // selected edge's condition and destination are edited — otherwise clicking
  // an edge would trade the settings pane for its "select a node" empty state.
  const settingsNodeId =
    selectedNodeId ?? (selectedEdgeId !== null ? edgeAddress(selectedEdgeId).nodeId : null);

  // Picking a node while the Global tab is showing should surface its
  // settings, not silently do nothing — the same reason a click always
  // updates `selectedNodeId` regardless of which tab is active.
  useEffect(() => {
    if (settingsNodeId) setRightTab("node");
  }, [settingsNodeId]);

  // A selection made in one version's graph shouldn't linger, half-relevant,
  // after a bulk replacement swaps the whole graph out from under it, and an
  // undo stack from the version just left would restore that version's graph
  // onto this one. Keyed off the same epoch as the remount, and for the same
  // reason: anything that clears the selection unmounts whatever field the
  // user is typing into, so it must never fire on an ordinary edit.
  useEffect(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setHistory(EMPTY_HISTORY);
  }, [canvasEpoch]);

  return (
    <ReactFlowProvider>
      <div className="flex h-full min-h-0 w-full">
        <NodePalette
          flow={flow}
          dispatch={record}
          readOnly={readOnly}
          agentDetails={agentDetails}
        />
        <Canvas
          key={canvasEpoch}
          flow={flow}
          dispatch={record}
          readOnly={readOnly}
          selectedNodeId={selectedNodeId}
          setSelectedNodeId={setSelectedNodeId}
          selectedEdgeId={selectedEdgeId}
          setSelectedEdgeId={setSelectedEdgeId}
          onUndo={undo}
          onRedo={redo}
          canUndo={history.past.length > 0}
          canRedo={history.future.length > 0}
        />
        <div className="flex h-full w-[380px] shrink-0 flex-col overflow-y-auto border-l border-line bg-card">
          <div className="sticky top-0 z-10 shrink-0 border-b border-line bg-card p-2">
            <PillTabs
              tabs={[
                { key: "global", label: "Global Settings" },
                { key: "node", label: "Node Settings" },
              ]}
              active={rightTab}
              onChange={(k) => setRightTab(k === "global" ? "global" : "node")}
            />
          </div>
          {rightTab === "global" ? (
            <>
              {globalHeader}
              <GlobalSettings flow={flow} dispatch={record} readOnly={readOnly} />
              {globalSections?.(record)}
            </>
          ) : (
            <NodeSettings
              flow={flow}
              dispatch={record}
              selectedNodeId={settingsNodeId}
              readOnly={readOnly}
            />
          )}
        </div>
      </div>
    </ReactFlowProvider>
  );
}
