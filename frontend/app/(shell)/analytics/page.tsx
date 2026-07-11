"use client";

import DonutCard from "@/components/analytics/DonutCard";
import StatTile from "@/components/analytics/StatTile";
import { CallCountsArea, ConcurrencyBars } from "@/components/analytics/TimeCharts";
import Button from "@/components/ui/Button";
import { UnderlineTabs } from "@/components/ui/Tabs";
import { api } from "@/lib/api";
import type { AnalyticsData } from "@/lib/types";
import {
  Calendar,
  GitBranch,
  ListFilter,
  MessageSquare,
  MoreHorizontal,
  Phone,
  Plus,
} from "lucide-react";
import { useEffect, useState } from "react";

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [tab, setTab] = useState("call");

  useEffect(() => {
    api.getAnalytics().then(setData);
  }, []);

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <h1 className="text-[17px] font-semibold">Analytics</h1>

      <UnderlineTabs
        className="mt-3"
        tabs={[
          {
            key: "call",
            label: (
              <span className="inline-flex items-center gap-1.5">
                <Phone className="size-3.5" /> Call Dashboard
              </span>
            ),
          },
          {
            key: "chat",
            label: (
              <span className="inline-flex items-center gap-1.5">
                <MessageSquare className="size-3.5" /> Chat Dashboard
              </span>
            ),
          },
          { key: "add", label: <Plus className="size-3.5" /> },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="mt-4 flex items-center gap-2">
        <Button>
          <Calendar className="size-3.5" />
          Jun 13 - Jul 10, 2026
        </Button>
        <Button>
          <ListFilter className="size-3.5" />
          Filter
        </Button>
        <Button>
          <GitBranch className="size-3.5" />
          Breakdown
        </Button>
        <div className="ml-auto flex items-center gap-2">
          <Button>
            <Plus className="size-3.5" />
            Add Chart
          </Button>
          <Button variant="ghost" aria-label="More">
            <MoreHorizontal className="size-4" />
          </Button>
        </div>
      </div>

      {data && tab === "call" && (
        <div className="mt-4 space-y-4 pb-8">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <StatTile label="Call Counts" value={String(data.call_counts)} />
            <StatTile label="Call Duration" value={`${data.avg_duration_s}s`} />
            <StatTile label="Call Latency" value={`${data.avg_latency_ms}ms`} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
            <CallCountsArea data={data.call_counts_series} />
            <ConcurrencyBars data={data.concurrency_series} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <DonutCard title="Call Successful" data={data.call_successful} />
            <DonutCard title="Disconnection Reason" data={data.disconnection_reason} />
            <DonutCard title="User Sentiment" data={data.user_sentiment} />
            <DonutCard title="Phone inbound/outbound" data={data.phone_direction} />
          </div>
        </div>
      )}

      {tab !== "call" && (
        <p className="py-24 text-center text-[13px] text-sub">
          No chat data yet. Deploy a chat agent to populate this dashboard.
        </p>
      )}
    </div>
  );
}
