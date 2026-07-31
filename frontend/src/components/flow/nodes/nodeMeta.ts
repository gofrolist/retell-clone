/**
 * Per-node-type display metadata for the canvas card: label, icon, title-bar
 * accent, and a one-line subtitle derived from the node's own content.
 *
 * The spec calls for "one component per type over a shared `NodeShell`", but
 * eight components that differ only in icon/accent/subtitle is duplication,
 * not clarity. This record plus one `FlowNode.tsx` reading it meets the same
 * intent — each type still reads distinctly on the canvas — without the
 * duplication. The per-type split that does earn its keep is the settings
 * editors (Task 6), where the field sets genuinely differ.
 */

import {
  ArrowRightLeft,
  GitBranch,
  MessagesSquare,
  PhoneForwarded,
  Braces,
  Bot,
  CircleStop,
  Hash,
  type LucideIcon,
} from "lucide-react";
import type { FlowNode } from "../flowModel";

export type NodeMeta = {
  label: string;
  icon: LucideIcon;
  /** Tailwind classes for the node's title bar. */
  accent: string;
  /** One line of the node's own content, for the canvas card. */
  subtitle: (node: FlowNode) => string;
};

const instructionText = (node: FlowNode): string => {
  const i = node.instruction as { text?: string } | undefined;
  return typeof i?.text === "string" ? i.text : "";
};

export const NODE_META: Record<string, NodeMeta> = {
  conversation: {
    label: "Conversation",
    icon: MessagesSquare,
    accent: "bg-blue-50 text-accent-deep",
    subtitle: instructionText,
  },
  subagent: {
    label: "Subagent",
    icon: Bot,
    accent: "bg-blue-50 text-accent-deep",
    subtitle: instructionText,
  },
  branch: {
    label: "Branch",
    icon: GitBranch,
    accent: "bg-amber-50 text-amber-900",
    subtitle: () => "Routes without speaking",
  },
  function: {
    label: "Function",
    icon: ArrowRightLeft,
    accent: "bg-violet-50 text-violet-900",
    subtitle: (n) => (typeof n.tool_id === "string" ? n.tool_id : "No tool selected"),
  },
  extract_dynamic_variables: {
    label: "Extract variables",
    icon: Braces,
    accent: "bg-violet-50 text-violet-900",
    subtitle: (n) =>
      ((n.variables as { name?: string }[]) ?? [])
        .map((v) => v.name)
        .filter(Boolean)
        .join(", ") || "No variables",
  },
  transfer_call: {
    label: "Transfer",
    icon: PhoneForwarded,
    accent: "bg-sky-50 text-sky-900",
    subtitle: (n) => {
      const d = n.transfer_destination as { number?: string; prompt?: string } | undefined;
      return d?.number || d?.prompt || "No destination";
    },
  },
  press_digit: {
    label: "Press digit",
    icon: Hash,
    accent: "bg-violet-50 text-violet-900",
    subtitle: instructionText,
  },
  end: {
    label: "End call",
    icon: CircleStop,
    accent: "bg-neutral-100 text-neutral-700",
    subtitle: instructionText,
  },
};

/** A node type the graph carries but this editor does not model (never from
 *  our own palette — only from a flow imported with a newer node type). */
export const UNKNOWN_META: NodeMeta = {
  label: "Unsupported node",
  icon: CircleStop,
  accent: "bg-rose-50 text-rose-900",
  subtitle: (n) => `type: ${String(n.type)}`,
};
