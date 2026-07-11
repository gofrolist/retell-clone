"use client";

import CopyId from "@/components/ui/CopyId";
import StatusDot from "@/components/ui/StatusDot";
import type { Call, Sentiment } from "@/lib/types";
import { cn, formatCallTime, formatCost, formatDuration, truncateId } from "@/lib/utils";

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

export default function CallsTable({
  calls,
  selectedId,
  onSelect,
}: {
  calls: Call[];
  selectedId?: string | null;
  onSelect: (call: Call) => void;
}) {
  return (
    <table className="w-full min-w-[960px] text-left">
      <thead className="sticky top-0 z-[1] bg-card">
        <tr className="border-b border-line text-[13px] text-sub">
          {[
            "Time",
            "Duration",
            "Channel Type",
            "Cost",
            "Session ID",
            "End Reason",
            "Session Status",
            "User Sentiment",
            "From",
          ].map((h) => (
            <th key={h} className="whitespace-nowrap px-3 py-2.5 font-medium first:pl-4">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {calls.map((c) => (
          <tr
            key={c.call_id}
            onClick={() => onSelect(c)}
            className={cn(
              "cursor-pointer border-b border-line/70 transition-colors",
              selectedId === c.call_id ? "bg-app" : "hover:bg-app/60",
            )}
          >
            <td className="whitespace-nowrap py-3 pl-4 pr-3">
              {formatCallTime(c.start_timestamp)}
            </td>
            <td className="px-3 py-3 tabular-nums">{formatDuration(c.duration_ms)}</td>
            <td className="px-3 py-3 text-sub">{c.channel_type}</td>
            <td className="px-3 py-3 tabular-nums">{formatCost(c.cost)}</td>
            <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
              <CopyId value={c.call_id} display={truncateId(c.call_id, 20)} />
            </td>
            <td className="whitespace-nowrap px-3 py-3">
              {c.disconnection_reason ? (
                <StatusDot
                  color={reasonColor(c.disconnection_reason)}
                  label={c.disconnection_reason.replace(/_/g, " ")}
                />
              ) : (
                <span className="text-sub">-</span>
              )}
            </td>
            <td className="whitespace-nowrap px-3 py-3">
              <StatusDot color={STATUS_COLOR[c.call_status] ?? "gray"} label={c.call_status} />
            </td>
            <td className="whitespace-nowrap px-3 py-3">
              <StatusDot color={SENTIMENT_COLOR[c.user_sentiment]} label={c.user_sentiment} />
            </td>
            <td className="whitespace-nowrap px-3 py-3 tabular-nums text-sub">
              {c.from_number}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
