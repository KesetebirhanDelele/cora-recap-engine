"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WorkerTrendResponse, WorkerTrendWorker } from "@/types";

const WORKER_COLORS = ["#6366f1", "#06b6d4", "#f59e0b", "#10b981", "#f43f5e", "#8b5cf6"];

function workerColor(i: number): string {
  return WORKER_COLORS[i % WORKER_COLORS.length];
}

function buildChartData(data: WorkerTrendResponse) {
  const now = new Date();
  now.setSeconds(0, 0);

  const buckets: string[] = [];
  for (let i = 59; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 60_000);
    buckets.push(d.toISOString().slice(0, 16));
  }

  const index: Record<string, Record<string, { jobs: number; avg: number | null }>> = {};
  for (const pt of data.points) {
    const b = pt.bucket.slice(0, 16);
    if (!index[b]) index[b] = {};
    index[b][pt.worker_id] = { jobs: pt.jobs, avg: pt.avg_duration_s };
  }

  return buckets.map((b, idx) => {
    const row: Record<string, unknown> = { time: idx % 10 === 0 ? b.slice(11, 16) : "" };
    for (const w of data.worker_ids) {
      const entry = index[b]?.[w.worker_id];
      row[`${w.worker_id}__jobs`] = entry?.jobs ?? 0;
      row[`${w.worker_id}__dur`] = entry?.avg ?? null;
    }
    return row;
  });
}

const thStyle: React.CSSProperties = {
  fontSize: "0.8rem",
  fontWeight: 700,
  color: "#0f172a",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
};

const subStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  color: "#64748b",
  marginBottom: "0.625rem",
};

function LegendDot({ color, bar }: { color: string; bar?: boolean }) {
  return bar ? (
    <span style={{ width: 10, height: 10, borderRadius: 2, background: color, display: "inline-block", flexShrink: 0 }} />
  ) : (
    <span style={{ width: 18, height: 2.5, borderRadius: 2, background: color, display: "inline-block", flexShrink: 0 }} />
  );
}

function WorkerLegend({ workers, bar }: { workers: WorkerTrendWorker[]; bar?: boolean }) {
  return (
    <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginTop: "0.5rem" }}>
      {workers.map((w, i) => (
        <div key={w.worker_id} style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.68rem", color: "#475569" }}>
          <LegendDot color={workerColor(i)} bar={bar} />
          {w.worker_id_short}
        </div>
      ))}
    </div>
  );
}

interface Props {
  data: WorkerTrendResponse;
}

export default function WorkerTrendChart({ data }: Props) {
  if (data.worker_ids.length === 0) {
    return (
      <div style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "1.5rem",
        textAlign: "center",
        color: "#94a3b8",
        fontSize: "0.8rem",
      }}>
        No worker activity in the last 60 minutes.
      </div>
    );
  }

  const chartData = buildChartData(data);
  const workers = data.worker_ids;

  const axisProps = {
    tick: { fontSize: 10, fill: "#94a3b8" },
    tickLine: false,
    axisLine: false,
  } as const;

  const gridProps = {
    strokeDasharray: "3 3" as const,
    stroke: "#f1f5f9",
    vertical: false,
  };

  const tooltipStyle: React.CSSProperties = {
    fontSize: "0.72rem",
    border: "1px solid #e2e8f0",
    borderRadius: 6,
    padding: "6px 10px",
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.875rem" }}>
        <span style={thStyle}>60-Minute Trend</span>
        <span style={{ fontSize: "0.65rem", color: "#94a3b8" }}>1-min buckets · last hour</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>
        {/* Jobs per minute — stacked bars */}
        <div>
          <div style={subStyle}>Jobs completed per minute</div>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="time" {...axisProps} interval={0} />
              <YAxis {...axisProps} allowDecimals={false} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name: string) => {
                  const wid = name.replace("__jobs", "");
                  const short = workers.find(w => w.worker_id === wid)?.worker_id_short ?? wid.slice(-8);
                  return [value, short];
                }}
              />
              {workers.map((w, i) => (
                <Bar
                  key={w.worker_id}
                  dataKey={`${w.worker_id}__jobs`}
                  stackId="stack"
                  fill={workerColor(i)}
                  opacity={0.82}
                  radius={i === workers.length - 1 ? [2, 2, 0, 0] : [0, 0, 0, 0]}
                  isAnimationActive={false}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
          <WorkerLegend workers={workers} bar />
        </div>

        {/* Avg duration per worker — lines */}
        <div>
          <div style={subStyle}>Avg job duration (seconds)</div>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="time" {...axisProps} interval={0} />
              <YAxis {...axisProps} tickFormatter={(v) => `${v}s`} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name: string) => {
                  if (value == null) return ["—", ""];
                  const wid = name.replace("__dur", "");
                  const short = workers.find(w => w.worker_id === wid)?.worker_id_short ?? wid.slice(-8);
                  return [`${value}s`, short];
                }}
              />
              {workers.map((w, i) => (
                <Line
                  key={w.worker_id}
                  dataKey={`${w.worker_id}__dur`}
                  stroke={workerColor(i)}
                  strokeWidth={1.5}
                  dot={false}
                  connectNulls={false}
                  type="monotone"
                  isAnimationActive={false}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
          <WorkerLegend workers={workers} />
        </div>
      </div>
    </div>
  );
}
