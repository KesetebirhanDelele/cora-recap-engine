"use client";

/**
 * Conversion Funnel — /conversion-funnel
 *
 * Shows where leads drop off across the call lifecycle:
 *   Total Calls → Picked Up → Engaged → Booked
 *
 * Layout (scrollable page with PageShell):
 *   ┌─────────────────────────────────────────┐
 *   │  Date filter (topbar right)             │
 *   ├─────────────────────┬───────────────────┤
 *   │  Funnel visual      │  Step table       │
 *   ├─────────────────────┴───────────────────┤
 *   │  Conversion rate trends (line chart)    │
 *   └─────────────────────────────────────────┘
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import { fetchMetrics, fetchVoicePerformance } from "@/lib/api";
import type { MetricsResponse, VoicePerformanceResponse } from "@/types";
import DateRangePicker from "@/components/voice/DateRangePicker";

// ── Intents that indicate genuine AI-detected engagement ──────────────────────
const ENGAGED_INTENTS = new Set([
  "enrolled", "re_engaged", "callback_request", "callback_with_time",
  "interested_not_now", "partial_engagement", "human_transfer_request",
  "failed_booking", "call_later_no_time", "request_sms", "request_email",
]);

function defaultDates() {
  const to = new Date();
  const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

const pct = (n: number | null, total: number) =>
  n === null || total === 0 ? "—" : `${((n / total) * 100).toFixed(1)}%`;

const drop = (from: number, to: number) =>
  from === 0 ? "—" : `-${(((from - to) / from) * 100).toFixed(1)}%`;

// ── Funnel bar visual ─────────────────────────────────────────────────────────

interface FunnelStep {
  label: string;
  count: number;
  color: string;
  prevCount?: number;
}

function FunnelBar({ steps }: { steps: FunnelStep[] }) {
  const max = steps[0]?.count ?? 1;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem" }}>
      {steps.map((step, i) => {
        const width = max === 0 ? 4 : Math.max(4, Math.round((step.count / max) * 100));
        const convRate =
          step.prevCount && step.prevCount > 0
            ? ((step.count / step.prevCount) * 100).toFixed(1)
            : null;
        return (
          <div key={step.label}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "0.25rem",
              }}
            >
              <span style={{ fontSize: "0.78rem", fontWeight: 600, color: "#374151" }}>
                {i + 1}. {step.label}
              </span>
              <span style={{ fontSize: "0.9rem", fontWeight: 800, color: "#0f172a" }}>
                {step.count.toLocaleString()}
              </span>
            </div>
            <div style={{ position: "relative", height: 28, background: "#f1f5f9", borderRadius: 6, overflow: "hidden" }}>
              <div
                style={{
                  width: `${width}%`,
                  height: "100%",
                  background: step.color,
                  borderRadius: 6,
                  transition: "width 0.4s ease",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "flex-end",
                  paddingRight: 8,
                }}
              >
                {convRate && (
                  <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "#ffffff", whiteSpace: "nowrap" }}>
                    {convRate}%
                  </span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Step conversion table ─────────────────────────────────────────────────────

function ConversionTable({ steps }: { steps: FunnelStep[] }) {
  const totalCalls = steps[0]?.count ?? 0;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
      <thead>
        <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
          {["Stage", "Count", "% of Total", "Step Conv.", "Drop-off"].map((h) => (
            <th
              key={h}
              style={{
                padding: "0.5rem 0.75rem",
                textAlign: h === "Stage" ? "left" : "right",
                fontWeight: 700,
                color: "#64748b",
                fontSize: "0.72rem",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {steps.map((step, i) => (
          <tr
            key={step.label}
            style={{
              borderBottom: "1px solid #f1f5f9",
              background: i % 2 === 0 ? "#ffffff" : "#fafafa",
            }}
          >
            <td style={{ padding: "0.5rem 0.75rem", color: "#1e293b", fontWeight: 600 }}>
              <span
                style={{
                  display: "inline-block",
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: step.color,
                  marginRight: 8,
                  verticalAlign: "middle",
                }}
              />
              {step.label}
            </td>
            <td style={{ padding: "0.5rem 0.75rem", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
              {step.count.toLocaleString()}
            </td>
            <td style={{ padding: "0.5rem 0.75rem", textAlign: "right", color: "#475569" }}>
              {pct(step.count, totalCalls)}
            </td>
            <td style={{ padding: "0.5rem 0.75rem", textAlign: "right", color: "#16a34a", fontWeight: 600 }}>
              {step.prevCount !== undefined
                ? step.prevCount === 0
                  ? "—"
                  : `${((step.count / step.prevCount) * 100).toFixed(1)}%`
                : "—"}
            </td>
            <td
              style={{
                padding: "0.5rem 0.75rem",
                textAlign: "right",
                color: step.prevCount !== undefined && step.prevCount > 0 ? "#dc2626" : "#94a3b8",
                fontWeight: 600,
              }}
            >
              {step.prevCount !== undefined ? drop(step.prevCount, step.count) : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Biggest drop highlight ────────────────────────────────────────────────────

function DropOffHighlight({ steps }: { steps: FunnelStep[] }) {
  let worstLabel = "";
  let worstPct = 0;
  for (const step of steps) {
    if (step.prevCount && step.prevCount > 0) {
      const lost = (step.prevCount - step.count) / step.prevCount;
      if (lost > worstPct) {
        worstPct = lost;
        worstLabel = step.label;
      }
    }
  }
  if (!worstLabel) return null;
  return (
    <div
      style={{
        background: "#fef2f2",
        border: "1px solid #fecaca",
        borderRadius: 8,
        padding: "0.625rem 1rem",
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
        fontSize: "0.82rem",
      }}
    >
      <span style={{ fontSize: "1rem" }}>⚠️</span>
      <span style={{ color: "#374151" }}>
        Biggest drop-off at{" "}
        <strong style={{ color: "#dc2626" }}>{worstLabel}</strong>:{" "}
        <strong style={{ color: "#dc2626" }}>{(worstPct * 100).toFixed(1)}%</strong> of
        previous-stage leads lost here.
      </span>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const CARD: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
};

const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  color: "#1e293b",
  boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
  fontSize: "0.8rem",
};

export default function ConversionFunnelPage() {
  const defaults = defaultDates();
  const [fromDate, setFromDate] = useState(defaults.from);
  const [toDate, setToDate] = useState(defaults.to);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [voice, setVoice] = useState<VoicePerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (from: string, to: string) => {
    setLoading(true);
    setError(null);
    try {
      const [m, v] = await Promise.all([
        fetchMetrics({
          from_date: from ? `${from}T00:00:00Z` : undefined,
          to_date: to ? `${to}T23:59:59Z` : undefined,
        }),
        fetchVoicePerformance({
          from_date: from ? `${from}T00:00:00Z` : undefined,
          to_date: to ? `${to}T23:59:59Z` : undefined,
        }),
      ]);
      setMetrics(m);
      setVoice(v);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(fromDate, toDate); }, [fromDate, toDate, load]);

  // ── Compute funnel steps ────────────────────────────────────────────────────
  const totalCalls = metrics?.kpis.total_calls ?? 0;
  const pickedUp = metrics?.kpis.pickup_rate != null
    ? Math.round(totalCalls * metrics.kpis.pickup_rate)
    : 0;
  const engagedCount = Object.entries(metrics?.ai.intent_distribution ?? {})
    .filter(([intent]) => ENGAGED_INTENTS.has(intent))
    .reduce((sum, [, count]) => sum + count, 0);
  const engaged = Math.min(engagedCount, pickedUp);
  const booked = metrics?.kpis.enrolled_count ?? 0;

  const steps: FunnelStep[] = [
    { label: "Total Calls",  count: totalCalls, color: "#3b82f6" },
    { label: "Picked Up",    count: pickedUp,   color: "#16a34a",  prevCount: totalCalls },
    { label: "Engaged",      count: engaged,    color: "#7c3aed",  prevCount: pickedUp },
    { label: "Booked",       count: booked,     color: "#059669",  prevCount: engaged },
  ];

  // ── Trend data (from voice time_series) ────────────────────────────────────
  const trendData = (voice?.time_series ?? []).map((pt) => ({
    date: pt.date,
    "Pickup Rate": pt.pickup_rate,
    "Booking Rate": pt.booking_rate,
  }));

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#f1f5f9",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: "#1e293b",
      }}
    >
      {/* ── Topbar ─────────────────────────────────────────────────────────── */}
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          height: 48,
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          display: "flex",
          alignItems: "center",
          padding: "0 1.5rem",
          gap: "0.875rem",
          boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
        }}
      >
        <Link
          href="/"
          style={{ color: "#94a3b8", textDecoration: "none", fontSize: "0.875rem", fontWeight: 500 }}
        >
          ← Dashboard
        </Link>
        <span style={{ width: 1, height: 16, background: "#e2e8f0" }} />
        <span style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b" }}>
          Conversion Funnel
        </span>
        {loading && (
          <span style={{ fontSize: "0.75rem", color: "#94a3b8", marginLeft: 4 }}>Refreshing…</span>
        )}
        {error && (
          <span style={{ fontSize: "0.75rem", color: "#ef4444", marginLeft: 4 }}>⚠ {error}</span>
        )}
        <div style={{ marginLeft: "auto" }}>
          <DateRangePicker
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={setFromDate}
            onToChange={setToDate}
          />
        </div>
      </div>

      {/* ── Content ────────────────────────────────────────────────────────── */}
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "1.5rem 1.5rem 3rem" }}>

        {/* Page heading */}
        <div style={{ marginBottom: "1.25rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.35rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.025em" }}>
            Conversion Funnel
          </h1>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
            Where leads drop off across the call lifecycle.
            {metrics && (
              <span style={{ color: "#94a3b8", marginLeft: 8 }}>
                {metrics.period.from.slice(0, 10)} → {metrics.period.to.slice(0, 10)}
              </span>
            )}
          </p>
        </div>

        {/* Drop-off alert */}
        {!loading && metrics && (
          <div style={{ marginBottom: "1rem" }}>
            <DropOffHighlight steps={steps} />
          </div>
        )}

        {/* ── Funnel + Table row ──────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>

          {/* Funnel visual */}
          <div style={{ ...CARD, padding: "1.25rem" }}>
            <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "1rem" }}>
              📉 Funnel Overview
            </div>
            <FunnelBar steps={steps} />
          </div>

          {/* Step table */}
          <div style={{ ...CARD, overflow: "hidden" }}>
            <div style={{ padding: "1.25rem 1.25rem 0.75rem", fontSize: "0.72rem", fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              📊 Step Breakdown
            </div>
            <ConversionTable steps={steps} />
          </div>
        </div>

        {/* ── Conversion rate trends ──────────────────────────────────────── */}
        <div style={{ ...CARD, padding: "1.25rem" }}>
          <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "0.75rem" }}>
            📈 Conversion Rates Over Time
            <span style={{ fontWeight: 400, color: "#94a3b8", marginLeft: 8, textTransform: "none", letterSpacing: 0 }}>
              — weekly buckets
            </span>
          </div>
          {trendData.length === 0 ? (
            <div style={{ height: 220, display: "flex", alignItems: "center", justifyContent: "center", color: "#94a3b8", fontSize: "0.85rem" }}>
              No trend data for this period.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trendData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="#e2e8f0" tick={{ fill: "#64748b", fontSize: 11 }} />
                <YAxis
                  stroke="#e2e8f0"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickFormatter={(v) => `${v}%`}
                  domain={[0, 100]}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v: number) => [`${v}%`]}
                />
                <Legend
                  wrapperStyle={{ fontSize: "0.78rem", color: "#475569" }}
                />
                <Line
                  type="monotone"
                  dataKey="Pickup Rate"
                  stroke="#16a34a"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="Booking Rate"
                  stroke="#059669"
                  strokeWidth={2}
                  dot={false}
                  strokeDasharray="5 3"
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

      </div>
    </div>
  );
}
