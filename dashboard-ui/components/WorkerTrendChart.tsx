"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WorkerTrendResponse, WorkerTrendWorker } from "@/types";

// One color per worker slot — stable order so the same worker always gets the same color.
const WORKER_COLORS = ["#6366f1", "#06b6d4", "#f59e0b", "#10b981", "#f43f5e", "#8b5cf6"];

function workerColor(i: number): string {
  return WORKER_COLORS[i % WORKER_COLORS.length];
}

// Build 36 × 10-minute buckets (6 hours) ending at the current floored 10-min boundary.
// Backend bucket keys are UTC ISO strings truncated to the minute ("2026-04-28T15:10").
// Frontend generates the same keys so points index cleanly.
function buildChartData(data: WorkerTrendResponse) {
  const BUCKET_MS  = 10 * 60 * 1000;
  const N_BUCKETS  = 36; // 6 hours

  const nowMs      = Date.now();
  const flooredMs  = Math.floor(nowMs / BUCKET_MS) * BUCKET_MS;

  const bucketKeys:   string[] = [];
  const bucketLabels: string[] = [];

  for (let i = N_BUCKETS - 1; i >= 0; i--) {
    const d = new Date(flooredMs - i * BUCKET_MS);
    bucketKeys.push(d.toISOString().slice(0, 16));             // UTC "YYYY-MM-DDTHH:MM"
    // Label on the hour boundary; otherwise blank — keeps the axis uncluttered.
    bucketLabels.push(
      d.getUTCMinutes() === 0
        ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
        : ""
    );
  }

  // Index backend points by (bucket_key → worker_id → stats)
  const index: Record<string, Record<string, { jobs: number; median: number | null }>> = {};
  for (const pt of data.points) {
    const b = pt.bucket.slice(0, 16);
    if (!index[b]) index[b] = {};
    index[b][pt.worker_id] = { jobs: pt.jobs, median: pt.median_duration_s };
  }

  return bucketKeys.map((b, idx) => {
    const row: Record<string, unknown> = { time: bucketLabels[idx] };
    for (const w of data.worker_ids) {
      const entry = index[b]?.[w.worker_id];
      row[`${w.worker_id}__jobs`]   = entry?.jobs   ?? 0;
      row[`${w.worker_id}__median`] = entry?.median  ?? null;
    }
    return row;
  });
}

// ── Shared axis / grid config ─────────────────────────────────────────────────

const axisProps = {
  tick:     { fontSize: 10, fill: "#94a3b8" },
  tickLine: false,
  axisLine: false,
} as const;

const gridProps = {
  strokeDasharray: "3 3" as const,
  stroke:          "#f1f5f9",
  vertical:        false,
};

const tooltipStyle: React.CSSProperties = {
  fontSize:     "0.72rem",
  border:       "1px solid #e2e8f0",
  borderRadius: 6,
  padding:      "6px 10px",
};

// ── Legend ────────────────────────────────────────────────────────────────────

function WorkerLegend({ workers, bar }: { workers: WorkerTrendWorker[]; bar?: boolean }) {
  return (
    <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginTop: "0.5rem" }}>
      {workers.map((w, i) => (
        <div
          key={w.worker_id}
          style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.68rem", color: "#475569" }}
        >
          {bar ? (
            <span style={{ width: 10, height: 10, borderRadius: 2, background: workerColor(i), opacity: 0.8, display: "inline-block", flexShrink: 0 }} />
          ) : (
            <span style={{ width: 18, height: 2.5, borderRadius: 2, background: workerColor(i), display: "inline-block", flexShrink: 0 }} />
          )}
          {w.worker_id_short}
        </div>
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data: WorkerTrendResponse;
}

export default function WorkerTrendChart({ data }: Props) {
  if (data.worker_ids.length === 0) {
    return (
      <div style={{
        background:    "#ffffff",
        border:        "1px solid #e2e8f0",
        borderRadius:  8,
        padding:       "1.5rem",
        textAlign:     "center",
        color:         "#94a3b8",
        fontSize:      "0.8rem",
      }}>
        No worker activity in the last 6 hours.
      </div>
    );
  }

  const chartData = buildChartData(data);
  const workers   = data.worker_ids;

  return (
    <div>
      {/* Section header */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.875rem" }}>
        <span style={{
          fontSize:        "0.8rem",
          fontWeight:      700,
          color:           "#0f172a",
          textTransform:   "uppercase",
          letterSpacing:   "0.06em",
        }}>
          Worker Activity — 6-Hour Trend
        </span>
        <span style={{ fontSize: "0.65rem", color: "#94a3b8" }}>
          10-min buckets · median duration
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>

        {/* ── Chart 1: Stacked area — job load ── */}
        <div>
          <div style={{ fontSize: "0.68rem", color: "#64748b", marginBottom: "0.625rem" }}>
            Worker Job Distribution (jobs per 10-min window)
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="time" {...axisProps} interval={0} />
              <YAxis {...axisProps} allowDecimals={false} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name: string) => {
                  const wid   = name.replace("__jobs", "");
                  const short = workers.find(w => w.worker_id === wid)?.worker_id_short ?? wid.slice(-8);
                  return [value, short];
                }}
              />
              {/* Render bottom-most layer last so top layers don't obscure small ones */}
              {[...workers].reverse().map((w, ri) => {
                const i = workers.length - 1 - ri;
                return (
                  <Area
                    key={w.worker_id}
                    dataKey={`${w.worker_id}__jobs`}
                    stackId="stack"
                    stroke={workerColor(i)}
                    fill={workerColor(i)}
                    fillOpacity={0.65}
                    strokeWidth={1}
                    type="monotone"
                    isAnimationActive={false}
                  />
                );
              })}
            </AreaChart>
          </ResponsiveContainer>
          <WorkerLegend workers={workers} bar />
        </div>

        {/* ── Chart 2: Lines — median duration ── */}
        <div>
          <div style={{ fontSize: "0.68rem", color: "#64748b", marginBottom: "0.625rem" }}>
            Median Job Duration by Worker (seconds)
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="time" {...axisProps} interval={0} />
              <YAxis {...axisProps} tickFormatter={(v) => `${v}s`} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, name: string) => {
                  if (value == null) return ["—", ""];
                  const wid   = name.replace("__median", "");
                  const short = workers.find(w => w.worker_id === wid)?.worker_id_short ?? wid.slice(-8);
                  return [`${value}s`, short];
                }}
              />
              {workers.map((w, i) => (
                <Line
                  key={w.worker_id}
                  dataKey={`${w.worker_id}__median`}
                  stroke={workerColor(i)}
                  strokeWidth={1.5}
                  dot={false}
                  connectNulls={false}
                  type="monotone"
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
          <WorkerLegend workers={workers} />
        </div>

      </div>
    </div>
  );
}
