"use client";

import ChartCard, { LegendItem } from "./ChartCard";
import { CHART_BLUE } from "./chartColors";
import type { StatPoint } from "@/lib/types";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const AXIS = { fontSize: 11, fill: "#9ca3af" };
const GRID = "#eef0f3";

const tooltipStyle = {
  fontSize: 12,
  borderRadius: 8,
  border: "1px solid #e6e8ec",
  boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
  padding: "6px 10px",
};

export function CallCountsArea({ data }: { data: StatPoint[] }) {
  return (
    <ChartCard title="Call Counts" legend={<LegendItem color={CHART_BLUE} label="Call counts" />}>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <defs>
              <linearGradient id="callCountsFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_BLUE} stopOpacity={0.28} />
                <stop offset="100%" stopColor={CHART_BLUE} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={GRID} strokeDasharray="4 4" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} minTickGap={48} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={46} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "#d8dbe0" }} />
            <Area
              type="monotone"
              dataKey="value"
              name="Call counts"
              stroke={CHART_BLUE}
              strokeWidth={2}
              fill="url(#callCountsFill)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

/** Generic day-series card (area or bars) for breakdown/chat/custom charts. */
export function SeriesCard({
  title,
  label,
  data,
  kind = "area",
  height = "h-64",
}: {
  title: string;
  label: string;
  data: StatPoint[];
  kind?: "area" | "bar";
  height?: string;
}) {
  const gradientId = `seriesFill-${title.replace(/[^a-zA-Z0-9]/g, "")}`;
  return (
    <ChartCard title={title} legend={<LegendItem color={CHART_BLUE} label={label} />}>
      <div className={height}>
        <ResponsiveContainer width="100%" height="100%">
          {kind === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={CHART_BLUE} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={CHART_BLUE} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={GRID} strokeDasharray="4 4" vertical={false} />
              <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} minTickGap={48} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={46} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "#d8dbe0" }} />
              <Area
                type="monotone"
                dataKey="value"
                name={label}
                stroke={CHART_BLUE}
                strokeWidth={2}
                fill={`url(#${gradientId})`}
              />
            </AreaChart>
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="4 4" vertical={false} />
              <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} minTickGap={40} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(0,0,0,0.03)" }} />
              <Bar
                dataKey="value"
                name={label}
                fill={CHART_BLUE}
                radius={[4, 4, 0, 0]}
                maxBarSize={14}
              />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

export function ConcurrencyBars({ data }: { data: StatPoint[] }) {
  return (
    <ChartCard
      title="Concurrency Used"
      legend={<LegendItem color={CHART_BLUE} label="Concurrency used" />}
    >
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
            <CartesianGrid stroke={GRID} strokeDasharray="4 4" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} minTickGap={40} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(0,0,0,0.03)" }} />
            <Bar
              dataKey="value"
              name="Concurrency used"
              fill={CHART_BLUE}
              radius={[4, 4, 0, 0]}
              maxBarSize={14}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
