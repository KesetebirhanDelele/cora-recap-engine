"use client";

/**
 * SystemAnomalies — System Anomalies page component.
 *
 * Aggregated intelligence layer — NOT a raw exception queue.
 * Surfaces patterns, spikes, and failure clusters across the exception dataset.
 * Operators use this page to diagnose systemic issues, not to action individual records.
 *
 * Sections:
 *   1. Spike Detection    — types spiking above their 7-day baseline
 *   2. Recurring Issues   — top exception types by total frequency (30 days)
 *   3. Failure Clusters   — contacts / entities with repeated failures (7 days)
 *   4. Anomaly Trend      — daily total exception count over 14 days
 */

import { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { fetchExceptionAnomalies } from "@/lib/api";
import type { ExceptionAnomaliesResponse, ExceptionSpike, ExceptionRecurring, ExceptionCluster } from "@/types";

// ── Category helpers ──────────────────────────────────────────────────────────

const CATEGORY: Record<string, string> = {
  unknown_call_status: "Call", call_pending: "Call", call_processing_failed: "Call",
  identity_resolution: "Call", tier_invalid: "Call",
  openai_error: "AI", openai_failed: "AI",
  ghl_update_failed: "CRM", ghl_auth_failed: "CRM", ghl_write_failed: "CRM",
  webhook_failure: "System", job_timeout: "System", data_validation_failure: "System",
  retry_budget_exhausted: "System", postgres_write_failed: "System",
};

const CATEGORY_COLOR: Record<string, string> = {
  Call: "#2563eb", AI: "#7c3aed", CRM: "#0891b2", System: "#64748b",
};

function getCategory(type: string): string {
  return CATEGORY[type] ?? "System";
}

function getCatColor(type: string): string {
  return CATEGORY_COLOR[getCategory(type)] ?? "#64748b";
}

// ── Design tokens ─────────────────────────────────────────────────────────────

const C = {
  card:   "#ffffff",
  border: "#e2e8f0",
  muted:  "#64748b",
  sub:    "#94a3b8",
  head:   "#0f172a",
};

const AXIS_STYLE  = { fill: "#64748b", fontSize: 11 };
const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  color: "#1e293b",
  boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
  fontSize: "0.8rem",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div style={{ marginBottom: "0.875rem" }}>
      <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700, color: C.head, letterSpacing: "-0.02em" }}>
        {title}
      </h2>
      <p style={{ margin: "0.2rem 0 0", fontSize: "0.8rem", color: C.muted }}>{subtitle}</p>
    </div>
  );
}

function Chip({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 9999,
        fontSize: "0.68rem",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color,
        background: bg,
        border: `1px solid ${color}30`,
      }}
    >
      {label}
    </span>
  );
}

// ── Section 1: Spike Detection ────────────────────────────────────────────────

function SpikeDetection({ spikes }: { spikes: ExceptionSpike[] }) {
  return (
    <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "1rem 1.125rem" }}>
      <SectionHeader
        title="Spike Detection"
        subtitle="Exception types with sudden increases in the last 24 hours compared to the prior 7-day baseline."
      />
      {spikes.length === 0 ? (
        <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, padding: "1rem", textAlign: "center", color: "#15803d", fontSize: "0.875rem" }}>
          No spikes detected — exception volumes are within normal range.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {spikes.map((spike) => {
            const catColor = getCatColor(spike.type);
            const isNew = spike.is_new_type;
            const factor = spike.spike_factor;
            return (
              <div
                key={spike.type}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.75rem",
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  borderLeft: "3px solid #ef4444",
                  borderRadius: 8,
                  padding: "0.625rem 0.875rem",
                  flexWrap: "wrap",
                }}
              >
                <Chip label={getCategory(spike.type)} color={catColor} bg={`${catColor}14`} />
                <code style={{ fontSize: "0.82rem", fontWeight: 700, color: "#dc2626", fontFamily: "monospace", flex: 1 }}>
                  {spike.type.replace(/_/g, " ")}
                </code>
                <div style={{ display: "flex", gap: "1rem", flexShrink: 0 }}>
                  <span style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    <span style={{ fontWeight: 700, color: "#dc2626", fontSize: "1rem" }}>{spike.recent_24h}</span>
                    {" "}in 24h
                  </span>
                  {isNew ? (
                    <Chip label="New type" color="#7c3aed" bg="#f5f3ff" />
                  ) : (
                    <span style={{ fontSize: "0.78rem", color: "#64748b" }}>
                      baseline <span style={{ fontWeight: 600 }}>{spike.baseline_daily_avg}/day</span>
                      {factor && <span style={{ color: "#dc2626", fontWeight: 700 }}> ×{factor}</span>}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ── Section 2: Recurring Issues ───────────────────────────────────────────────

function RecurringIssues({ recurring }: { recurring: ExceptionRecurring[] }) {
  return (
    <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "1rem 1.125rem" }}>
      <SectionHeader
        title="Recurring Issues"
        subtitle="Exception types by total frequency over the last 30 days. High counts indicate systemic problems."
      />
      {recurring.length === 0 ? (
        <div style={{ color: C.sub, fontSize: "0.875rem" }}>No exceptions in the last 30 days.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                {["Type", "Category", "Severity", "Total", "Open", "Resolved", "Last Seen"].map((h) => (
                  <th key={h} style={{ textAlign: "left", padding: "0.4rem 0.625rem", color: C.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", fontSize: "0.68rem", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recurring.map((r, i) => {
                const catColor = getCatColor(r.type);
                const isCrit = r.severity === "critical";
                return (
                  <tr
                    key={r.type}
                    style={{ borderBottom: `1px solid ${C.border}`, background: i % 2 === 0 ? "#f8fafc" : "#ffffff" }}
                  >
                    <td style={{ padding: "0.5rem 0.625rem", fontFamily: "monospace", fontWeight: 700, color: C.head, maxWidth: 200 }}>
                      {r.type.replace(/_/g, " ")}
                    </td>
                    <td style={{ padding: "0.5rem 0.625rem" }}>
                      <Chip label={getCategory(r.type)} color={catColor} bg={`${catColor}14`} />
                    </td>
                    <td style={{ padding: "0.5rem 0.625rem" }}>
                      <Chip
                        label={r.severity}
                        color={isCrit ? "#dc2626" : "#1d4ed8"}
                        bg={isCrit ? "#fee2e2" : "#dbeafe"}
                      />
                    </td>
                    <td style={{ padding: "0.5rem 0.625rem", fontWeight: 800, color: C.head }}>{r.total}</td>
                    <td style={{ padding: "0.5rem 0.625rem", color: r.open > 0 ? "#dc2626" : C.muted, fontWeight: r.open > 0 ? 700 : 400 }}>{r.open}</td>
                    <td style={{ padding: "0.5rem 0.625rem", color: "#16a34a" }}>{r.resolved}</td>
                    <td style={{ padding: "0.5rem 0.625rem", color: C.sub, whiteSpace: "nowrap" }}>{timeAgo(r.last_seen)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── Section 3: Failure Clusters ───────────────────────────────────────────────

function FailureClusters({ clusters }: { clusters: ExceptionCluster[] }) {
  return (
    <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "1rem 1.125rem" }}>
      <SectionHeader
        title="Failure Clusters"
        subtitle="Contacts or entities with repeated failures in the last 7 days — likely candidates for investigation."
      />
      {clusters.length === 0 ? (
        <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, padding: "1rem", textAlign: "center", color: "#15803d", fontSize: "0.875rem" }}>
          No failure clusters — no single entity has accumulated repeated failures.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {clusters.map((c) => (
            <div
              key={c.entity_id}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "1rem",
                background: c.failure_count >= 5 ? "#fef2f2" : "#f8fafc",
                border: `1px solid ${c.failure_count >= 5 ? "#fecaca" : C.border}`,
                borderLeft: `3px solid ${c.failure_count >= 5 ? "#ef4444" : "#f59e0b"}`,
                borderRadius: 8,
                padding: "0.625rem 0.875rem",
                flexWrap: "wrap",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                  <code style={{ fontSize: "0.8rem", fontWeight: 700, color: C.head, fontFamily: "monospace" }}>
                    {c.entity_id}
                  </code>
                  {c.entity_type && (
                    <span style={{ fontSize: "0.68rem", color: C.muted, background: "#f1f5f9", border: `1px solid ${C.border}`, borderRadius: 4, padding: "1px 6px" }}>
                      {c.entity_type}
                    </span>
                  )}
                </div>
                <div style={{ marginTop: "0.375rem", display: "flex", flexWrap: "wrap", gap: "0.375rem" }}>
                  {c.exception_types.map((t) => {
                    const cc = getCatColor(t);
                    return <Chip key={t} label={t.replace(/_/g, " ")} color={cc} bg={`${cc}14`} />;
                  })}
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", flexShrink: 0, gap: 4 }}>
                <span style={{ fontSize: "1.5rem", fontWeight: 800, color: c.failure_count >= 5 ? "#dc2626" : "#d97706", letterSpacing: "-0.04em", lineHeight: 1 }}>
                  {c.failure_count}
                </span>
                <span style={{ fontSize: "0.68rem", color: C.sub }}>failures</span>
                <span style={{ fontSize: "0.68rem", color: C.sub }}>{timeAgo(c.last_failure)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── Section 4: Anomaly Trend ──────────────────────────────────────────────────

function AnomalyTrend({ trend }: { trend: { date: string; count: number }[] }) {
  const data = trend.map((p) => ({ date: p.date.slice(5), count: p.count })); // MM-DD

  return (
    <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "1rem 1.125rem" }}>
      <SectionHeader
        title="Anomaly Frequency"
        subtitle="Total daily exception count over the last 14 days — rising trends indicate worsening system health."
      />
      {data.length === 0 ? (
        <div style={{ textAlign: "center", color: C.sub, fontSize: "0.8rem", padding: "2rem 0" }}>
          No data available
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={data}>
            <CartesianGrid vertical={false} stroke="#f1f5f9" />
            <XAxis dataKey="date" tick={AXIS_STYLE} tickLine={false} axisLine={false} interval="preserveStartEnd" />
            <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ stroke: "#e2e8f0" }} />
            <Line
              type="monotone"
              dataKey="count"
              stroke="#ef4444"
              strokeWidth={2}
              dot={{ fill: "#ef4444", r: 3 }}
              activeDot={{ r: 5 }}
              name="Exceptions"
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SystemAnomalies() {
  const [data, setData]     = useState<ExceptionAnomaliesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchExceptionAnomalies();
      setData(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return <div style={{ color: "#64748b", fontSize: "0.875rem", padding: "2rem 0" }}>Loading anomaly data…</div>;
  }

  if (error) {
    return (
      <div style={{ background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, padding: "0.75rem 1rem", color: "#dc2626", fontSize: "0.875rem" }}>
        ⚠ {error}
      </div>
    );
  }

  if (!data) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      <SpikeDetection spikes={data.spikes} />
      <AnomalyTrend trend={data.anomaly_trend} />
      <RecurringIssues recurring={data.recurring} />
      <FailureClusters clusters={data.clusters} />
    </div>
  );
}
