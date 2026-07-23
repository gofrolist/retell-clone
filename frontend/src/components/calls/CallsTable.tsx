"use client";

import CopyId from "@/components/ui/CopyId";
import StatusDot from "@/components/ui/StatusDot";
import type { Call, Sentiment } from "@/lib/types";
import { cn, formatCallTime, formatCost, formatDuration, pressableProps, truncateId } from "@/lib/utils";
import type { ReactNode } from "react";

type DotColor = "green" | "red" | "blue" | "gray" | "orange";

// Backend reasons are Retell-style snake_case ("user_hangup", "dial_no_answer",
// "error_telephony", …) — match on keywords instead of exact strings.
function reasonColor(reason: string): DotColor {
  const r = reason.toLowerCase();
  if (r.includes("hangup")) return "green";
  if (r.includes("voicemail")) return "orange";
  if (r.includes("transfer")) return "blue";
  if (r.includes("no_answer") || r.includes("no answer") || r.includes("error") || r.includes("busy"))
    return "red";
  return "gray";
}

const STATUS_COLOR: Record<string, DotColor> = {
  ended: "gray",
  not_connected: "gray",
  registered: "gray",
  ongoing: "blue",
  error: "red",
};

const SENTIMENT_COLOR: Record<Sentiment, DotColor> = {
  Positive: "green",
  Neutral: "blue",
  Negative: "red",
  Unknown: "gray",
};

/** Column catalog: id → header + cell renderer. Order here is display order. */
export const CALL_COLUMNS: { id: string; header: string; cell: (c: Call) => ReactNode }[] = [
  {
    id: "time",
    header: "Time",
    cell: (c) => formatCallTime(c.start_timestamp),
  },
  {
    id: "duration",
    header: "Duration",
    cell: (c) => <span className="tabular-nums">{formatDuration(c.duration_ms)}</span>,
  },
  {
    id: "channel",
    header: "Channel Type",
    cell: (c) => <span className="text-sub">{c.channel_type}</span>,
  },
  {
    id: "cost",
    header: "Cost",
    cell: (c) => <span className="tabular-nums">{formatCost(c.cost)}</span>,
  },
  {
    id: "session_id",
    header: "Session ID",
    cell: (c) => (
      <span onClick={(e) => e.stopPropagation()}>
        <CopyId value={c.call_id} display={truncateId(c.call_id, 20)} />
      </span>
    ),
  },
  {
    id: "end_reason",
    header: "End Reason",
    cell: (c) =>
      c.disconnection_reason ? (
        <StatusDot
          color={reasonColor(c.disconnection_reason)}
          label={c.disconnection_reason.replace(/_/g, " ")}
        />
      ) : (
        <span className="text-sub">-</span>
      ),
  },
  {
    id: "status",
    header: "Session Status",
    cell: (c) => <StatusDot color={STATUS_COLOR[c.call_status] ?? "gray"} label={c.call_status} />,
  },
  {
    id: "sentiment",
    header: "User Sentiment",
    cell: (c) => <StatusDot color={SENTIMENT_COLOR[c.user_sentiment]} label={c.user_sentiment} />,
  },
  {
    id: "agent",
    header: "Agent",
    cell: (c) => <span className="text-sub">{c.agent_name}</span>,
  },
  {
    id: "from",
    header: "From",
    cell: (c) => <span className="tabular-nums text-sub">{c.from_number}</span>,
  },
  {
    id: "to",
    header: "To",
    cell: (c) => <span className="tabular-nums text-sub">{c.to_number}</span>,
  },
];

/** Columns shown when the user hasn't customized anything (Retell's default set). */
export const DEFAULT_CALL_COLUMNS = [
  "time",
  "duration",
  "channel",
  "cost",
  "session_id",
  "end_reason",
  "status",
  "sentiment",
  "from",
];

export default function CallsTable({
  calls,
  selectedId,
  onSelect,
  visibleColumns = DEFAULT_CALL_COLUMNS,
}: {
  calls: Call[];
  selectedId?: string | null;
  onSelect: (call: Call) => void;
  visibleColumns?: string[];
}) {
  const columns = CALL_COLUMNS.filter((c) => visibleColumns.includes(c.id));
  return (
    <table className="w-full min-w-[960px] text-left">
      <thead className="sticky top-0 z-[1] bg-card">
        <tr className="border-b border-line text-[13px] text-sub">
          {columns.map((col) => (
            <th key={col.id} className="whitespace-nowrap px-3 py-2.5 font-medium first:pl-4">
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {calls.map((c) => (
          <tr
            key={c.call_id}
            {...pressableProps(
              `Call from ${c.from_number} at ${formatCallTime(c.start_timestamp)}`,
              () => onSelect(c),
            )}
            className={cn(
              "cursor-pointer border-b border-line/70 transition-colors",
              selectedId === c.call_id ? "bg-app" : "hover:bg-app/60",
            )}
          >
            {columns.map((col) => (
              <td key={col.id} className="whitespace-nowrap px-3 py-3 first:pl-4">
                {col.cell(c)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
