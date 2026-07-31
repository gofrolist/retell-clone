"use client";

import type { ComponentType } from "react";
import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { TextInput } from "@/components/ui/Field";
import { cn, truncateId } from "@/lib/utils";
import type { RawConversationFlow } from "@/lib/api";
import { knownVariables, type FlowAction, type FlowNode } from "../flowModel";
import { NODE_META, UNKNOWN_META } from "../nodes/nodeMeta";
import EdgeList from "./EdgeList";
import ConversationSettings from "./ConversationSettings";
import BranchSettings from "./BranchSettings";
import FunctionSettings from "./FunctionSettings";
import TransferSettings from "./TransferSettings";
import EndSettings from "./EndSettings";
import ExtractSettings from "./ExtractSettings";
import PressDigitSettings from "./PressDigitSettings";

/** Props every per-type settings editor shares — see the seven `*Settings.tsx` siblings. */
export type NodeSettingsProps = {
  node: FlowNode;
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  variables: string[];
};

/**
 * One editor per supported node type (`conversation`/`subagent` share
 * `ConversationSettings` — the worker's `_enter_conversation` handles both
 * identically). Deliberately a lookup, not a switch: every entry has the
 * exact same `NodeSettingsProps` signature, so there is nothing a switch
 * would buy over indexing this record by `node.type`.
 */
const EDITORS: Record<string, ComponentType<NodeSettingsProps>> = {
  conversation: ConversationSettings,
  subagent: ConversationSettings,
  branch: BranchSettings,
  function: FunctionSettings,
  transfer_call: TransferSettings,
  end: EndSettings,
  extract_dynamic_variables: ExtractSettings,
  press_digit: PressDigitSettings,
};

/**
 * The settings pane's whole contents: header, then fields common to every
 * node type (name, id, edges, "set as start"), then the per-type body below.
 * `readOnly` freezes the entire pane with a native `<fieldset disabled>` —
 * the same trick `src/app/agents/[id]/page.tsx` uses — rather than threading
 * a `readOnly` prop through every field of every per-type editor.
 */
export default function NodeSettings({
  flow,
  dispatch,
  selectedNodeId,
  readOnly,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  selectedNodeId: string | null;
  readOnly: boolean;
}) {
  const nodes = Array.isArray(flow.nodes) ? (flow.nodes as FlowNode[]) : [];
  const node = nodes.find((n) => n.id === selectedNodeId);

  if (!node) {
    return (
      <div className="p-4 text-[13px] text-sub">
        Select a node on the canvas, or drag one in from the palette on the left, to edit its
        settings.
      </div>
    );
  }

  const meta = NODE_META[node.type] ?? UNKNOWN_META;
  const Icon = meta.icon;
  const Editor = EDITORS[node.type];
  const variables = knownVariables(flow);
  const isStart = flow.start_node_id === node.id;
  const name = typeof node.name === "string" ? node.name : "";

  return (
    <fieldset disabled={readOnly} className="min-w-0 space-y-4 p-4">
      <div className="flex items-center gap-2">
        <span
          className={cn("flex size-7 shrink-0 items-center justify-center rounded-md", meta.accent)}
        >
          <Icon className="size-4" />
        </span>
        <span className="text-[13px] font-medium text-ink">{meta.label}</span>
      </div>

      <div>
        <TextInput
          value={name}
          onChange={(e) =>
            dispatch({ type: "patchNode", nodeId: node.id, patch: { name: e.target.value } })
          }
          placeholder="Node name"
        />
        <div className="mt-1.5 flex items-center justify-between">
          <CopyId value={node.id} display={truncateId(node.id, 18)} />
          {!isStart && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => dispatch({ type: "setStartNode", nodeId: node.id })}
            >
              Set as start node
            </Button>
          )}
        </div>
      </div>

      <div>
        <p className="mb-1.5 text-[11px] font-semibold tracking-wide text-faint uppercase">
          Edges
        </p>
        <EdgeList node={node} flow={flow} dispatch={dispatch} variables={variables} />
      </div>

      <div className="border-t border-line pt-4">
        {Editor ? (
          <Editor node={node} flow={flow} dispatch={dispatch} variables={variables} />
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-sub">
              This node type (“{String(node.type)}”) is not editable here yet — likely imported
              from a newer flow than this editor knows about. Showing its raw data so nothing is
              hidden.
            </p>
            <pre className="max-h-80 overflow-auto rounded-lg border border-line bg-app p-2 text-[11px] text-ink">
              {JSON.stringify(node, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </fieldset>
  );
}
