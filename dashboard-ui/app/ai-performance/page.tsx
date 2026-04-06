"use client";

/**
 * AI Performance — /ai-performance
 *
 * Focused strictly on AI behavior — no business KPIs.
 *
 * Sections:
 *   1. AI Quality tiles  (blank transcript rate, unknown intent %, calls analyzed)
 *   2. Intent Distribution (bar chart)
 *   3. Consent Distribution (bar chart)
 *   4. Intent → Outcome table
 *   5. Trend charts: blank transcript rate + unknown intent % over time
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, Label,
  LineChart, Line, CartesianGrid, Legend,
} from "recharts";
import { fetchMetrics, fetchAiTimeSeries } from "@/lib/api";
import type { MetricsResponse, AiTimeSeriesResponse } from "@/types";
import DateRangePicker from "@/components/voice/DateRangePicker";

function defaultDates() {
  const to = new Date();
  const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

// ── Intent color map ─────────────────────────────────────────────────────────

const INTENT_COLORS: Record<string, string> = {
  enrolled:               "#16a34a",
  re_engaged:             "#2563eb",
  callback_request:       "#7c3aed",
  callback_with_time:     "#7c3aed",
  interested_not_now:     "#0891b2",
  not_interested:         "#ea580c",
  do_not_call:            "#dc2626",
  partial_engagement:     "#d97706",
  human_transfer_request: "#9333ea",
  low_confidence_audio:   "#94a3b8",
  uncertain:              "#cbd5e1",
  request_sms:            "#06b6d4",
  request_email:          "#06b6d4",
};

// Intents that lead to a booking (enrolled = booked in this system)
const BOOKING_INTENTS = new Set(["enrolled"]);

const AXIS_STYLE = { fill: "#64748b", fontSize: 11 };
const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  color: "#1e293b",
  boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
  fontSize: "0.8rem",
};

// ── Shared card style ─────────────────────────────────────────────────────────

const CARD: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
};

const SECTION_LABEL: React.CSSProperties = {
  fontSize: "0.72rem",
  fontWeight: 700,
  color: "#64748b",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  marginBottom: "0.875rem",
};

// ── AI Quality tile ───────────────────────────────────────────────────────────

function QualityTile({
  label, value, accent, sub,
}: { label: string; value: string; accent: string; sub?: string }) {
  return (
    <div
      style={{
        ...CARD,
        borderTop: `2px solid ${accent}`,
        padding: "0.875rem 1rem",
        flex: "1 1 160px",
      }}
    >
      <div style={{ fontSize: "0.68rem", color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.5rem", fontWeight: 800, color: "#0f172a" }}>{value}</div>
      {sub && <div style={{ fontSize: "0.72rem", color: "#94a3b8", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ── Intent → Outcome table ────────────────────────────────────────────────────

function IntentOutcomeTable({ intentDist }: { intentDist: Record<string, number> }) {
  const total = Object.values(intentDist).reduce((s, v) => s + v, 0);
  const rows = Object.entries(intentDist)
    .sort((a, b) => b[1] - a[1])
    .map(([intent, count]) => ({
      intent,
      count,
      share: total > 0 ? ((count / total) * 100).toFixed(1) : "—",
      bookingRate: BOOKING_INTENTS.has(intent) ? "100%" : "0%",
    }));

  if (rows.length === 0) {
    return <p style={{ color: "#94a3b8", fontSize: "0.85rem" }}>No data for this period.</p>;
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
        <thead>
          <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
            {["Intent", "Count", "% of Calls", "Booking Rate"].map((h) => (
              <th
                key={h}
                style={{
                  padding: "0.5rem 0.75rem",
                  textAlign: h === "Intent" ? "left" : "right",
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
          {rows.map((row, i) => (
            <tr
              key={row.intent}
              style={{ borderBottom: "1px solid #f1f5f9", background: i % 2 === 0 ? "#ffffff" : "#fafafa" }}
            >
              <td style={{ padding: "0.45rem 0.75rem", color: "#1e293b" }}>
                <span
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: INTENT_COLORS[row.intent] ?? "#3b82f6",
                    marginRight: 8,
                    verticalAlign: "middle",
                  }}
                />
                {row.intent.replace(/_/g, " ")}
              </td>
              <td style={{ padding: "0.45rem 0.75rem", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                {row.count.toLocaleString()}
              </td>
              <td style={{ padding: "0.45rem 0.75rem", textAlign: "right", color: "#475569" }}>
                {row.share}%
              </td>
              <td
                style={{
                  padding: "0.45rem 0.75rem",
                  textAlign: "right",
                  fontWeight: 600,
                  color: BOOKING_INTENTS.has(row.intent) ? "#16a34a" : "#94a3b8",
                }}
              >
                {row.bookingRate}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AiPerformancePage() {
  const defaults = defaultDates();
  const [fromDate, setFromDate] = useState(defaults.from);
  const [toDate, setToDate] = useState(defaults.to);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [timeSeries, setTimeSeries] = useState<AiTimeSeriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (from: string, to: string) => {
    setLoading(true);
    setError(null);
    try {
      const [m, ts] = await Promise.all([
        fetchMetrics({
          from_date: from ? `${from}T00:00:00Z` : undefined,
          to_date: to ? `${to}T23:59:59Z` : undefined,
        }),
        fetchAiTimeSeries({
          from_date: from ? `${from}T00:00:00Z` : undefined,
          to_date: to ? `${to}T23:59:59Z` : undefined,
        }),
      ]);
      setMetrics(m);
      setTimeSeries(ts);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(fromDate, toDate); }, [fromDate, toDate, load]);

  // ── Derived values ──────────────────────────────────────────────────────────
  const intentDist = metrics?.ai.intent_distribution ?? {};
  const consentDist = metrics?.ai.consent_distribution ?? {};
  const totalIntents = Object.values(intentDist).reduce((s, v) => s + v, 0);
  const totalCalls = metrics?.kpis.total_calls ?? 0;
  const blankRate = metrics?.ai.blank_transcript_rate;
  const unknownCount = intentDist["low_confidence_audio"] ?? 0;
  const unknownRate = totalIntents > 0 ? ((unknownCount / totalIntents) * 100).toFixed(1) : null;

  const intentChartData = Object.entries(intentDist)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({ name: name.replace(/_/g, " "), count }));

  const consentChartData = Object.entries(consentDist).map(([name, count]) => ({ name, count }));

  const trendData = (timeSeries?.time_series ?? []).map((pt) => ({
    date: pt.date,
    "Blank Transcript %": pt.blank_transcript_rate,
    "Unknown Intent %": pt.unknown_intent_rate,
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
          AI Performance
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
            AI Performance
          </h1>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
            Intent detection, consent analysis & AI quality metrics.
            {metrics && (
              <span style={{ color: "#94a3b8", marginLeft: 8 }}>
                {metrics.period.from.slice(0, 10)} → {metrics.period.to.slice(0, 10)}
              </span>
            )}
          </p>
        </div>

        {/* ── Section 1: AI Quality tiles ──────────────────────────────────── */}
        <div style={{ ...SECTION_LABEL }}>🧠 AI Quality Metrics</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.875rem", marginBottom: "1.5rem" }}>
          <QualityTile
            label="Calls Analyzed"
            value={totalCalls.toLocaleString()}
            accent="#3b82f6"
            sub="call events in period"
          />
          <QualityTile
            label="Blank Transcript Rate"
            value={blankRate !== null && blankRate !== undefined ? `${(blankRate * 100).toFixed(1)}%` : "—"}
            accent={blankRate !== null && blankRate !== undefined && blankRate > 0.1 ? "#d97706" : "#16a34a"}
            sub="calls with no usable transcript"
          />
          <QualityTile
            label="Unknown Intent %"
            value={unknownRate !== null ? `${unknownRate}%` : "—"}
            accent={unknownRate !== null && parseFloat(unknownRate) > 15 ? "#d97706" : "#3b82f6"}
            sub="low_confidence_audio detections"
          />
          <QualityTile
            label="Intents Classified"
            value={totalIntents.toLocaleString()}
            accent="#7c3aed"
            sub="calls with a detected intent"
          />
        </div>

        {/* ── Section 2 & 3: Intent + Consent side-by-side ─────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: "1rem", marginBottom: "1.5rem" }}>

          {/* Intent Distribution */}
          <div style={{ ...CARD, padding: "1.25rem" }}>
            <div style={SECTION_LABEL}>📊 Intent Distribution</div>
            {intentChartData.length === 0 ? (
              <p style={{ color: "#94a3b8", fontSize: "0.85rem" }}>No data for this period.</p>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={intentChartData} layout="vertical" margin={{ top: 4, right: 40, left: 8, bottom: 4 }}>
                  <XAxis type="number" stroke="#e2e8f0" tick={AXIS_STYLE} />
                  <YAxis type="category" dataKey="name" width={165} stroke="#e2e8f0" tick={AXIS_STYLE} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#0000000a" }} />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                    {intentChartData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={INTENT_COLORS[entry.name.replace(/ /g, "_")] ?? "#3b82f6"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Consent Distribution */}
          <div style={{ ...CARD, padding: "1.25rem" }}>
            <div style={SECTION_LABEL}>✅ Consent Distribution</div>
            {consentChartData.length === 0 ? (
              <p style={{ color: "#94a3b8", fontSize: "0.85rem" }}>No data for this period.</p>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={consentChartData} margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                  <XAxis dataKey="name" stroke="#e2e8f0" tick={AXIS_STYLE} />
                  <YAxis stroke="#e2e8f0" tick={AXIS_STYLE} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#0000000a" }} />
                  <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]}>
                    {consentChartData.map((_, i) => (
                      <Cell key={i} fill={["#16a34a", "#dc2626", "#d97706", "#3b82f6"][i % 4]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* ── Section 4: Intent → Outcome table ───────────────────────────── */}
        <div style={{ ...CARD, marginBottom: "1.5rem", overflow: "hidden" }}>
          <div style={{ padding: "1.25rem 1.25rem 0.75rem", ...SECTION_LABEL }}>
            🗂️ Intent → Outcome Mapping
          </div>
          <IntentOutcomeTable intentDist={intentDist} />
        </div>

        {/* ── Section 5: Trend charts ──────────────────────────────────────── */}
        <div style={{ ...CARD, padding: "1.25rem" }}>
          <div style={SECTION_LABEL}>
            📈 AI Error & Quality Trends
            <span style={{ fontWeight: 400, color: "#94a3b8", marginLeft: 8, textTransform: "none", letterSpacing: 0 }}>
              — weekly buckets
            </span>
          </div>
          {trendData.length === 0 ? (
            <div
              style={{
                height: 200,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#94a3b8",
                fontSize: "0.85rem",
              }}
            >
              No trend data for this period. Try a longer date range.
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
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v: number) => [`${v !== null ? v.toFixed(1) : "—"}%`]}
                />
                <Legend wrapperStyle={{ fontSize: "0.78rem", color: "#475569" }} />
                <Line
                  type="monotone"
                  dataKey="Blank Transcript %"
                  stroke="#d97706"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="Unknown Intent %"
                  stroke="#94a3b8"
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
