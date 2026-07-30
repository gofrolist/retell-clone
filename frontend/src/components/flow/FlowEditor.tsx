"use client";

// External stylesheets are allowed anywhere in the app directory, including
// colocated client components — see `node_modules/next/dist/docs/01-app/
// 01-getting-started/11-css.md`'s "External stylesheets" section.
import "@xyflow/react/dist/style.css";

import {
  useCallback,
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
import { edgeAddress, toReactFlow, type FlowEdgeData } from "./flowGraph";
import type { FlowAction } from "./flowModel";
import NodePalette, { NODE_DRAG_MIME } from "./NodePalette";
import FlowNode from "./nodes/FlowNode";
import NoteNode from "./nodes/NoteNode";

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
        } else if (change.type === "position") {
          // Only apply on drag end, or the 800 ms autosave (Task 8) fires on
          // every pixel of a drag.
          if (readOnly || change.dragging !== false || !change.position) continue;
          dispatch({ type: "moveNode", nodeId: change.id, position: change.position });
        } else if (change.type === "remove") {
          if (readOnly) continue;
          dispatch({ type: "deleteNode", nodeId: change.id });
          setSelectedNodeId((current) => (current === change.id ? null : current));
        }
      }
    },
    [dispatch, readOnly, setSelectedNodeId],
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
 * canvas (center) · settings pane (right, a placeholder until Task 6 wires
 * up `<NodeSettings>`). `FlowEditor` owns only selection state
 * (`selectedNodeId`) — every graph mutation goes out through `dispatch`, and
 * `nodes`/`edges` are always derived from `flow` (see `Canvas` above), never
 * held as a second source of truth.
 */
export default function FlowEditor({
  flow,
  dispatch,
  readOnly,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  return (
    <ReactFlowProvider>
      <div className="flex h-full min-h-0 w-full">
        <NodePalette dispatch={dispatch} readOnly={readOnly} />
        <Canvas
          flow={flow}
          dispatch={dispatch}
          readOnly={readOnly}
          selectedNodeId={selectedNodeId}
          setSelectedNodeId={setSelectedNodeId}
        />
        <div className="h-full w-[320px] shrink-0 overflow-y-auto border-l border-line bg-card">
          {/* Task 6 replaces this placeholder with <NodeSettings>, reading
              `selectedNodeId` and mutating through `dispatch`. */}
          <div className="p-4 text-[13px] text-sub">
            {selectedNodeId ? `Selected node: ${selectedNodeId}` : "Select a node to edit its settings."}
          </div>
        </div>
      </div>
    </ReactFlowProvider>
  );
}
