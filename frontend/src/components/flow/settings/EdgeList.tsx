"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Trash2, TriangleAlert } from "lucide-react";
import Select from "@/components/ui/Select";
import type { RawConversationFlow } from "@/lib/api";
import {
  EDGE_SHAPES,
  iterNodeEdges,
  type EdgeShape,
  type FlowAction,
  type FlowEdge,
  type FlowNode,
  type TransitionCondition,
} from "../flowModel";
import ConditionEditor from "./ConditionEditor";

/**
 * Heading + one-line runtime explanation per shape — the single highest-
 * value copy in this pane, because none of these five behaviours is
 * guessable from the field name alone. Mirrors
 * `worker/src/arhiteq_worker/flow.py`'s `iter_node_edges` docstring exactly.
 */
const SHAPE_HEADING: Record<EdgeShape, string> = {
  edges: "Transitions",
  else_edge: "Fallback",
  edge: "On failure",
  always_edge: "Always",
  skip_response_edge: "Say and continue",
};

const SHAPE_EXPLANATION: Record<EdgeShape, string> = {
  edges: "Offered to the model, or evaluated as equations.",
  else_edge: "Taken when nothing else matches.",
  edge: "Taken when the transfer cannot be completed.",
  always_edge: "Taken on the caller's next turn, unconditionally.",
  skip_response_edge: "The node speaks its line, then moves on without waiting for the caller.",
};

/**
 * `always_edge`/`skip_response_edge` are never offered to the model
 * (`_enter_conversation` in `flow_runtime.py` follows them automatically), so
 * there is no model-facing condition to edit — only a destination.
 */
const NO_CONDITION_SHAPES = new Set<EdgeShape>(["always_edge", "skip_response_edge"]);

function summarize(edge: FlowEdge): string {
  const condition = edge.transition_condition;
  if (condition && typeof condition === "object") {
    if (condition.type === "prompt" && typeof condition.prompt === "string" && condition.prompt.trim()) {
      return condition.prompt;
    }
    if (condition.type === "equation") {
      const equations = Array.isArray(condition.equations) ? condition.equations : [];
      if (equations.length > 0) {
        const joiner = condition.operator === "||" ? " || " : " && ";
        return equations
          .map((eq) => {
            const e = (eq ?? {}) as Record<string, unknown>;
            return `${e.left ?? ""} ${e.operator ?? ""} ${e.right ?? ""}`.trim();
          })
          .join(joiner);
      }
    }
  }
  return "No condition set";
}

function EdgeRow({
  node,
  otherNodes,
  shape,
  index,
  edge,
  dispatch,
  variables,
}: {
  node: FlowNode;
  otherNodes: FlowNode[];
  shape: EdgeShape;
  index: number;
  edge: FlowEdge;
  dispatch: (action: FlowAction) => void;
  variables: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  const destination = typeof edge.destination_node_id === "string" ? edge.destination_node_id : "";
  const dangling = destination === "";
  const showCondition = !NO_CONDITION_SHAPES.has(shape);

  const patch = (patch: Record<string, unknown>) =>
    dispatch({ type: "patchEdge", nodeId: node.id, shape, index, patch });

  return (
    <div className="space-y-2 rounded-lg border border-line p-2.5">
      <div className="flex items-center gap-1.5">
        <Select
          value={destination}
          onChange={(v) => patch({ destination_node_id: v })}
          options={[
            { value: "", label: "No destination" },
            ...otherNodes.map((n) => ({
              value: n.id,
              label: typeof n.name === "string" && n.name ? n.name : n.id,
            })),
          ]}
          className="min-w-0 grow"
        />
        {showCondition && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 rounded p-1.5 text-faint hover:bg-app hover:text-ink cursor-pointer"
            aria-label={expanded ? "Collapse condition" : "Edit condition"}
          >
            {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        )}
        <button
          type="button"
          onClick={() => dispatch({ type: "deleteEdge", nodeId: node.id, shape, index })}
          className="shrink-0 rounded p-1.5 text-faint hover:bg-app hover:text-bad cursor-pointer"
          aria-label="Delete edge"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>

      {!showCondition && <p className="text-xs text-faint">Fires without asking the model.</p>}
      {showCondition && !expanded && (
        <p className="truncate text-xs text-sub" title={summarize(edge)}>
          {summarize(edge)}
        </p>
      )}
      {showCondition && expanded && (
        <ConditionEditor
          condition={(edge.transition_condition ?? {}) as TransitionCondition}
          onChange={(next) => patch({ transition_condition: next })}
          variables={variables}
        />
      )}

      {dangling && (
        <p className="flex items-start gap-1.5 rounded-md bg-red-50 px-2 py-1.5 text-xs text-bad">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
          No destination set. On a live call this is a dead end: the worker treats a dangling
          fallback as unresolvable and ends the call here.
        </p>
      )}
    </div>
  );
}

/**
 * Every edge a node carries, grouped by shape with a heading and runtime
 * explanation per group (see `SHAPE_HEADING`/`SHAPE_EXPLANATION`). Shared by
 * every per-type settings editor via `NodeSettings`, which renders this once,
 * above the per-type body.
 */
export default function EdgeList({
  node,
  flow,
  dispatch,
  variables,
}: {
  node: FlowNode;
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  variables: string[];
}) {
  const otherNodes = (Array.isArray(flow.nodes) ? (flow.nodes as FlowNode[]) : []).filter(
    (n) => n.id !== node.id,
  );

  const grouped = new Map<EdgeShape, { edge: FlowEdge; index: number }[]>();
  for (const { shape, edge, index } of iterNodeEdges(node)) {
    const list = grouped.get(shape) ?? [];
    list.push({ edge, index });
    grouped.set(shape, list);
  }

  if (grouped.size === 0) {
    return <p className="text-xs text-faint">No transitions from this node yet.</p>;
  }

  return (
    <div className="space-y-4">
      {EDGE_SHAPES.filter((shape) => grouped.has(shape)).map((shape) => (
        <div key={shape} className="space-y-2">
          <div>
            <p className="text-[13px] font-medium text-ink">{SHAPE_HEADING[shape]}</p>
            <p className="text-xs text-sub">{SHAPE_EXPLANATION[shape]}</p>
          </div>
          {(grouped.get(shape) ?? []).map(({ edge, index }) => (
            <EdgeRow
              key={`${shape}-${index}-${typeof edge.id === "string" ? edge.id : ""}`}
              node={node}
              otherNodes={otherNodes}
              shape={shape}
              index={index}
              edge={edge}
              dispatch={dispatch}
              variables={variables}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
