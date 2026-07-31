"use client";

import { cn } from "@/lib/utils";
import { useReactFlow } from "@xyflow/react";
import { useCallback, type DragEvent } from "react";
import { NODE_TYPES, type FlowAction } from "./flowModel";
import { NODE_META } from "./nodes/nodeMeta";

/** `dataTransfer` mime type a palette drag carries; `FlowEditor.onDrop` reads it. */
export const NODE_DRAG_MIME = "application/arhiteq-node";

/**
 * Left rail: one row per supported node type (`NODE_TYPES` — the exact seven
 * the worker accepts; an eighth type must never appear here). Clicking a row
 * dispatches `addNode` near the current viewport centre. Dragging a row onto
 * the canvas lets `FlowEditor`'s `onDrop` place it at the exact drop point —
 * the palette never needs the canvas transform for that path, it only sets
 * `dataTransfer`.
 */
export default function NodePalette({
  dispatch,
  readOnly,
}: {
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
}) {
  const { screenToFlowPosition } = useReactFlow();

  const addNodeNearCentre = useCallback(
    (nodeType: string) => {
      if (readOnly) return;
      const centre = screenToFlowPosition({
        x: window.innerWidth / 2,
        y: window.innerHeight / 2,
      });
      // A small jitter so repeated clicks on the same type don't stack the
      // new nodes exactly on top of one another.
      dispatch({
        type: "addNode",
        nodeType,
        position: {
          x: centre.x + (Math.random() * 40 - 20),
          y: centre.y + (Math.random() * 40 - 20),
        },
      });
    },
    [dispatch, readOnly, screenToFlowPosition],
  );

  const onDragStart = (event: DragEvent<HTMLButtonElement>, nodeType: string) => {
    if (readOnly) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(NODE_DRAG_MIME, nodeType);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="flex h-full w-[180px] shrink-0 flex-col overflow-y-auto border-r border-line bg-card">
      <div className="px-3 pt-3 pb-1.5 text-[11px] font-semibold tracking-wide text-faint uppercase">
        Nodes
      </div>
      <div className="flex flex-col gap-1 px-2 pb-3">
        {NODE_TYPES.map((nodeType) => {
          const meta = NODE_META[nodeType];
          if (!meta) return null;
          const Icon = meta.icon;
          return (
            <button
              key={nodeType}
              type="button"
              draggable={!readOnly}
              disabled={readOnly}
              onDragStart={(event) => onDragStart(event, nodeType)}
              onClick={() => addNodeNearCentre(nodeType)}
              className={cn(
                "flex items-center gap-2 rounded-lg border border-transparent px-2.5 py-2 text-left text-[13px] text-ink transition-colors",
                readOnly
                  ? "cursor-not-allowed opacity-50"
                  : "cursor-grab hover:border-line hover:bg-app active:cursor-grabbing",
              )}
            >
              <Icon className="size-4 shrink-0 text-sub" />
              <span className="truncate">{meta.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
