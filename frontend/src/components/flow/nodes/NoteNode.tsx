import type { NodeProps } from "@xyflow/react";
import type { NoteNodeData } from "../flowGraph";

/**
 * A plain, non-interactive sticky note (`type: "note"` in `toReactFlow`'s
 * output). No resize handle for v1 — round-tripping its position is enough.
 */
function NoteNode({ data }: NodeProps) {
  const { note } = data as unknown as NoteNodeData;
  const content = typeof note.content === "string" ? note.content : "";

  return (
    <div className="h-full w-full overflow-hidden rounded-md border border-amber-200 bg-amber-50 p-2.5 text-[12px] whitespace-pre-wrap text-amber-900 shadow-sm">
      {content}
    </div>
  );
}

export default NoteNode;
