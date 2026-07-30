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
  type SetStateAction,
} from "react";
import {
  Background,
  Controls,
  MiniMap,
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
import { edgeAddress, nodeChangeAction, toReactFlow, type FlowEdgeData } from "./flowGraph";
import type { FlowAction } from "./flowModel";
import NodePalette, { NODE_DRAG_MIME } from "./NodePalette";
import FlowNode from "./nodes/FlowNode";
import NoteNode from "./nodes/NoteNode";
import GlobalSettings from "./settings/GlobalSettings";
import NodeSettings from "./settings/NodeSettings";

// Module-level constants: a fresh object literal each render makes React
// Flow remount every node/edge and log a warning.
const nodeTypes: NodeTypes = { flowNode: FlowNode, note: NoteNode };
const edgeTypes: EdgeTypes = {};

function Canvas({
  flow,
  dispatch,
  readOnly,
  selectedNodeId,
  setSelectedNodeId,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
  selectedNodeId: string | null;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
}) {
  const { screenToFlowPosition } = useReactFlow();

  // The flow object is the single source of truth: `nodes`/`edges` are always
  // DERIVED from it, never a second source of truth held in local state.
  // Every mutation leaves through `dispatch`.
  const { nodes, edges } = useMemo(() => {
    const graph = toReactFlow(flow);
    return {
      nodes: graph.nodes.map((node) => ({
        ...node,
        selected: node.id === selectedNodeId,
      })),
      edges: graph.edges.map((edge) => ({
        ...edge,
        label: (edge.data as FlowEdgeData | undefined)?.label,
      })),
    };
  }, [flow, selectedNodeId]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const change of changes) {
        if (change.type === "select") {
          if (change.selected) {
            setSelectedNodeId(change.id);
          } else {
            setSelectedNodeId((current) => (current === change.id ? null : current));
          }
          continue;
        }
        if (readOnly) continue;
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
    },
    [dispatch, flow, readOnly, setSelectedNodeId],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      if (readOnly) return;
      for (const change of changes) {
        if (change.type !== "remove") continue;
        const { nodeId, shape, index } = edgeAddress(change.id);
        dispatch({ type: "deleteEdge", nodeId, shape, index });
      }
    },
    [dispatch, readOnly],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly || !connection.source || !connection.target) return;
      dispatch({
        type: "connect",
        nodeId: connection.source,
        shape: "edges",
        destinationNodeId: connection.target,
      });
    },
    [dispatch, readOnly],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      if (readOnly) return;
      const nodeType = event.dataTransfer.getData(NODE_DRAG_MIME);
      if (!nodeType) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
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

  return (
    <div className="h-full min-w-0 flex-1" onDrop={onDrop} onDragOver={onDragOver}>
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
        fitView
      >
        <Background />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}

/**
 * The three-pane conversation-flow canvas: node palette (left) · React Flow
 * canvas (center) · settings pane (right, `<NodeSettings>`). `FlowEditor`
 * owns only selection state (`selectedNodeId`) — every graph mutation goes
 * out through `dispatch`, and `nodes`/`edges` are always derived from `flow`
 * (see `Canvas` above), never held as a second source of truth.
 *
 * `canvasEpoch` (the caller passes `agent.version`) is a remount key for the
 * canvas only — see the "Fix wave" section of `.superpowers/sdd/
 * task-10-report.md`. Browser-verified root cause: after a *bulk* graph
 * replacement (switching to view a different published version, restoring a
 * version as a new draft, or the silent draft-fork on the first edit after a
 * publish — all of which change `agent.version`) React and the DOM update
 * correctly (confirmed via the accessibility tree and a live DOM query — the
 * new text really is there), but Chromium's compositor sometimes keeps
 * painting the pre-update pixels for the affected node(s) indefinitely, with
 * no re-render, resize, or wait fixing it — only a forced reflow does. A
 * `key` change is the standard, reliable way to force one: it unmounts and
 * remounts every DOM node in the canvas, so there is nothing stale left to
 * paint over. Ordinary incremental edits (typing, dragging) never change
 * `agent.version`, so they don't remount and keep their viewport/selection.
 */
export default function FlowEditor({
  flow,
  dispatch,
  readOnly,
  canvasEpoch,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
  canvasEpoch: number;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"node" | "global">("node");

  // Picking a node while the Global tab is showing should surface its
  // settings, not silently do nothing — the same reason a click always
  // updates `selectedNodeId` regardless of which tab is active.
  useEffect(() => {
    if (selectedNodeId) setRightTab("node");
  }, [selectedNodeId]);

  // A selection made in one version's graph shouldn't linger, half-relevant,
  // after a bulk replacement swaps the whole graph out from under it.
  useEffect(() => {
    setSelectedNodeId(null);
  }, [canvasEpoch]);

  return (
    <ReactFlowProvider>
      <div className="flex h-full min-h-0 w-full">
        <NodePalette dispatch={dispatch} readOnly={readOnly} />
        <Canvas
          key={canvasEpoch}
          flow={flow}
          dispatch={dispatch}
          readOnly={readOnly}
          selectedNodeId={selectedNodeId}
          setSelectedNodeId={setSelectedNodeId}
        />
        <div className="flex h-full w-[320px] shrink-0 flex-col overflow-y-auto border-l border-line bg-card">
          <div className="sticky top-0 z-10 shrink-0 border-b border-line bg-card p-2">
            <PillTabs
              tabs={[
                { key: "node", label: "Node" },
                { key: "global", label: "Global Settings" },
              ]}
              active={rightTab}
              onChange={(k) => setRightTab(k === "global" ? "global" : "node")}
            />
          </div>
          {rightTab === "global" ? (
            <GlobalSettings flow={flow} dispatch={dispatch} readOnly={readOnly} />
          ) : (
            <NodeSettings
              flow={flow}
              dispatch={dispatch}
              selectedNodeId={selectedNodeId}
              readOnly={readOnly}
            />
          )}
        </div>
      </div>
    </ReactFlowProvider>
  );
}
