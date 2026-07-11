"use client";

import ChartCard from "./ChartCard";
import { sliceColor } from "./chartColors";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

export default function DonutCard({
  title,
  data,
}: {
  title: string;
  data: { name: string; value: number }[];
}) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return (
    <ChartCard title={title}>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip
              contentStyle={{
                fontSize: 12,
                borderRadius: 8,
                border: "1px solid #e6e8ec",
                boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                padding: "6px 10px",
              }}
              formatter={(value) => [
                `${value} (${Math.round(((value as number) / total) * 100)}%)`,
              ]}
            />
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius="62%"
              outerRadius="95%"
              paddingAngle={2}
              stroke="#ffffff"
              strokeWidth={2}
            >
              {data.map((d, i) => (
                <Cell key={d.name} fill={sliceColor(d.name, i)} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-3 space-y-1">
        {data.map((d, i) => (
          <div key={d.name} className="flex items-center gap-1.5 text-xs">
            <span
              className="size-2.5 rounded-[3px] shrink-0"
              style={{ background: sliceColor(d.name, i) }}
            />
            <span className="grow truncate text-sub">{d.name}</span>
            <span className="tabular-nums font-medium">{d.value}</span>
            <span className="w-9 text-right tabular-nums text-faint">
              {Math.round((d.value / total) * 100)}%
            </span>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}
