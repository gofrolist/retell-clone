"use client";

import { PillTabs } from "@/components/ui/Tabs";
import type { RawConversationFlow } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useReactFlow } from "@xyflow/react";
import { Layers } from "lucide-react";
import { useCallback, useState, type DragEvent, type ReactNode } from "react";
import { NODE_TYPES, type FlowAction } from "./flowModel";
import { NODE_META } from "./nodes/nodeMeta";

/** `dataTransfer` mime type a palette drag carries; `FlowEditor.onDrop` reads it. */
export const NODE_DRAG_MIME = "application/arhiteq-node";

type Component = { conversation_flow_component_id?: string; name?: string; nodes?: unknown[] };

/**
 * The flow's subflows (`components[]` on the wire), as the rail lists them.
 * They are stored and round-tripped in full but neither executed nor edited
 * here — see `docs/ARCHITECTURE.md` — so the tab shows what a flow carries
 * rather than pretending to be an editor for it.
 */
function SubflowList({ flow }: { flow: RawConversationFlow }) {
  const components = (Array.isArray(flow.components) ? flow.components : []) as Component[];

  if (components.length === 0) {
    return (
      <p className="px-3 py-2 text-[12px] leading-relaxed text-sub">
        No subflows. A flow imported from Retell keeps any it has; they are stored and
        published unchanged, but are not editable here.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1 px-2">
      {components.map((component, index) => (
        <div
          key={component.conversation_flow_component_id ?? index}
          className="rounded-lg border border-line bg-app px-2.5 py-2"
        >
          <div className="flex items-center gap-2">
            <Layers className="size-4 shrink-0 text-sub" />
            <span className="truncate text-[13px] text-ink">
              {component.name || `Subflow ${index + 1}`}
            </span>
          </div>
          <p className="mt-0.5 pl-6 text-[11px] text-faint">
            {Array.isArray(component.nodes) ? component.nodes.length : 0} nodes · read-only
          </p>
        </div>
      ))}
    </div>
  );
}

/**
 * Left rail: a Node/Subflows tab switcher over one row per supported node type
 * (`NODE_TYPES` — the exact eight the worker accepts; a ninth type must never
 * appear here), with the agent's cost/latency/token estimates pinned to the
 * bottom. Clicking a row dispatches `addNode` near the current viewport
 * centre. Dragging a row onto the canvas lets `FlowEditor`'s `onDrop` place it
 * at the exact drop point — the palette never needs the canvas transform for
 * that path, it only sets `dataTransfer`.
 */
export default function NodePalette({
  flow,
  dispatch,
  readOnly,
  agentDetails,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
  /** The agent-details card pinned below the list; omitted, nothing renders. */
  agentDetails?: ReactNode;
}) {
  const { screenToFlowPosition } = useReactFlow();
  const [tab, setTab] = useState<"nodes" | "subflows">("nodes");

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
    <div className="flex h-full w-[200px] shrink-0 flex-col border-r border-line bg-card">
      <div className="shrink-0 border-b border-line p-2">
        <PillTabs
          className="w-full"
          tabs={[
            { key: "nodes", label: "Node" },
            { key: "subflows", label: "Subflows" },
          ]}
          active={tab}
          onChange={(key) => setTab(key === "subflows" ? "subflows" : "nodes")}
        />
      </div>

      {/* The list scrolls; the details card below it does not. */}
      <div className="min-h-0 grow overflow-y-auto py-2">
        {tab === "subflows" ? (
          <SubflowList flow={flow} />
        ) : (
          <div className="flex flex-col gap-1 px-2">
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
        )}
      </div>

      {agentDetails && <div className="shrink-0 border-t border-line p-2">{agentDetails}</div>}
    </div>
  );
}
