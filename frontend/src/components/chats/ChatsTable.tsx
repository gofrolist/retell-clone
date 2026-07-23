"use client";

import CopyId from "@/components/ui/CopyId";
import StatusDot from "@/components/ui/StatusDot";
import type { RawChat } from "@/lib/api";
import { cn, formatCallTime, formatDuration, pressableProps, truncateId } from "@/lib/utils";

const STATUS_COLOR: Record<string, "blue" | "gray" | "red"> = {
  ongoing: "blue",
  ended: "gray",
  error: "red",
};

export default function ChatsTable({
  chats,
  agentNames,
  selectedId,
  onSelect,
}: {
  chats: RawChat[];
  agentNames: Record<string, string>;
  selectedId?: string | null;
  onSelect: (chat: RawChat) => void;
}) {
  return (
    <table className="w-full min-w-[860px] text-left">
      <thead className="sticky top-0 z-[1] bg-card">
        <tr className="border-b border-line text-[13px] text-sub">
          {["Time", "Duration", "Channel Type", "Session ID", "Session Status", "Messages", "Agent"].map(
            (h) => (
              <th key={h} className="whitespace-nowrap px-3 py-2.5 font-medium first:pl-4">
                {h}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {chats.map((c) => {
          const started = c.start_timestamp ?? 0;
          const duration = c.end_timestamp && started ? c.end_timestamp - started : 0;
          return (
            <tr
              key={c.chat_id}
              {...pressableProps(`Chat at ${formatCallTime(started)}`, () => onSelect(c))}
              className={cn(
                "cursor-pointer border-b border-line/70 transition-colors",
                selectedId === c.chat_id ? "bg-app" : "hover:bg-app/60",
              )}
            >
              <td className="whitespace-nowrap py-3 pl-4 pr-3">{formatCallTime(started)}</td>
              <td className="px-3 py-3 tabular-nums">
                {duration ? formatDuration(duration) : "-"}
              </td>
              <td className="px-3 py-3 text-sub">chat</td>
              <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                <CopyId value={c.chat_id} display={truncateId(c.chat_id, 20)} />
              </td>
              <td className="whitespace-nowrap px-3 py-3">
                <StatusDot color={STATUS_COLOR[c.chat_status] ?? "gray"} label={c.chat_status} />
              </td>
              <td className="px-3 py-3 tabular-nums">{c.message_with_tool_calls?.length ?? 0}</td>
              <td className="whitespace-nowrap px-3 py-3 text-sub">
                {agentNames[c.agent_id] ?? truncateId(c.agent_id, 16)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
