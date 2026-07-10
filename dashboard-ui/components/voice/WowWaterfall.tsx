"use client";

import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Cell, ReferenceLine, ResponsiveContainer, LabelList,
} from "recharts";
import type { TooltipProps } from "recharts";

interface Props {
  wowChanges: Record<string, number | null>;
  height?: number | string;
}

const METRIC_LABELS: { key: string; label: string; fullLabel: string }[] = [
  { key: "unique_contacts",       label: "UC",  fullLabel: "Unique Contacts" },
  { key: "booking_rate",          label: "BR",  fullLabel: "Booking Rate" },
  { key: "total_calls",           label: "C",   fullLabel: "Calls" },
  { key: "completion_rate",       label: "CR",  fullLabel: "Completion Rate" },
  { key: "avg_call_duration_sec", label: "CD",  fullLabel: "Call Duration" },
  { key: "pickup_rate",           label: "PR",  fullLabel: "Pickup Rate" },
  { key: "voicemail_rate",        label: "VR",  fullLabel: "Voicemail Rate" },
  { key: "failed_rate",           label: "FR",  fullLabel: "Failed Rate" },
];

function CustomTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  const val = entry?.value as number;
  const isNull = (entry?.payload as { isNull?: boolean })?.isNull;
  const fullLabel = (entry?.payload as { fullLabel?: string })?.fullLabel ?? "";
  const isUp = val >= 0;
  const color = isNull ? "#e2e8f0" : isUp ? "#16a34a" : "#dc2626";
  return (
    <div
      style={{
        background: "#ffffff",
        border: `1px solid ${color}50`,
        borderLeft: `3px solid ${color}`,
        borderRadius: 6,
        padding: "0.5rem 0.75rem",
        boxShadow: "0 2px 8px rgba(0,0,0,0.1)",
        fontSize: "0.95rem",
        minWidth: 170,
      }}
    >
      <div style={{ fontWeight: 700, color: "#1e293b", marginBottom: 6, fontSize: "1rem" }}>
        {fullLabel}
      </div>
      {isNull ? (
        <div style={{ color: "#94a3b8", fontStyle: "italic" }}>No prior period data</div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: 2,
              background: color,
              flexShrink: 0,
            }}
          />
          <span style={{ color, fontWeight: 700, fontSize: "0.95rem" }}>
            {isUp ? "▲" : "▼"} {Math.abs(val).toFixed(1)}%
          </span>
          <span style={{ color: "#94a3b8", fontSize: "0.72rem" }}>vs last week</span>
        </div>
      )}
    </div>
  );
}

function LabelFormatter({ x, y, width, value }: { x?: number; y?: number; width?: number; value?: number }) {
  if (value === null || value === undefined || !x || !y || !width) return null;
  const isUp = value >= 0;
  const labelY = isUp ? (y ?? 0) - 6 : (y ?? 0) + 16;
  return (
    <text
      x={(x ?? 0) + (width ?? 0) / 2}
      y={labelY}
      fill={isUp ? "#16a34a" : "#dc2626"}
      fontSize={10}
      fontWeight={700}
      textAnchor="middle"
    >
      {isUp ? "▲" : "▼"}{Math.abs(value).toFixed(1)}%
    </text>
  );
}

export default function WowWaterfall({ wowChanges, height = "100%" }: Props) {
  const data = METRIC_LABELS.map(({ key, label, fullLabel }) => ({
    metric: label,
    fullLabel,
    wow: wowChanges[key] ?? 0,
    isNull: wowChanges[key] === null,
  }));

  const allNull = data.every((d) => d.isNull);
  if (allNull) {
    return (
      <div style={{ color: "#94a3b8", padding: "2rem", textAlign: "center", fontSize: "0.875rem" }}>
        No prior period data for WoW comparison
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 18, right: 12, left: 4, bottom: 28 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis
          dataKey="metric"
          stroke="#e2e8f0"
          tick={{ fill: "#475569", fontSize: 12 }}
          interval={0}
          label={{
            value: "Metric",
            position: "insideBottom",
            offset: -24,
            style: { fill: "#94a3b8", fontSize: 13, fontWeight: 600 },
          }}
        />
        <YAxis
          stroke="#e2e8f0"
          tick={{ fill: "#64748b", fontSize: 13 }}
          tickFormatter={(v) => `${v}%`}
          label={{
            value: "WoW Change (%)",
            angle: -90,
            position: "insideLeft",
            offset: 12,
            style: { fill: "#94a3b8", fontSize: 13, fontWeight: 600 },
          }}
        />
        <Tooltip content={<CustomTooltip />} cursor={{ fill: "#f8fafc" }} />
        <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={1.5} />
        <Bar dataKey="wow" radius={[3, 3, 0, 0]}>
          <LabelList content={<LabelFormatter />} dataKey="wow" />
          {data.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={entry.isNull ? "#e2e8f0" : entry.wow >= 0 ? "#16a34a" : "#dc2626"}
              fillOpacity={entry.isNull ? 0.5 : 0.85}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
