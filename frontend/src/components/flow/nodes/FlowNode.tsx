import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import type { FlowNodeData } from "../flowGraph";
import { NODE_META, UNKNOWN_META } from "./nodeMeta";
import NodeShell from "./NodeShell";

/**
 * React Flow's custom node component for every flow node (`type: "flowNode"`
 * in `toReactFlow`'s output). Wrapped in `memo`: React Flow re-renders every
 * node on every store change, and the canvas visibly stutters at ~18 nodes
 * without it.
 */
function FlowNode({ data, selected }: NodeProps) {
  const { node, isStart, isGlobal } = data as unknown as FlowNodeData;
  const meta = NODE_META[node.type] ?? UNKNOWN_META;
  const title = typeof node.name === "string" && node.name ? node.name : meta.label;

  return (
    <NodeShell
      icon={meta.icon}
      accent={meta.accent}
      title={title}
      subtitle={meta.subtitle(node)}
      isStart={isStart}
      isGlobal={isGlobal}
      selected={Boolean(selected)}
    />
  );
}

export default memo(FlowNode);
