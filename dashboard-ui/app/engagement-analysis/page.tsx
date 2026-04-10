"use client";

/**
 * Engagement Analysis — /engagement-analysis
 *
 * Sections:
 *   1. Filter bar:
 *        - Campaign (All / New Lead / Cold Lead)  — filters on lead_state.campaign_name
 *        - Voice Agent (All / NewLead / ColdLead / Inbound) — filters on call_events.voice_agent
 *        - Call Direction (All / Outbound / Inbound) — filters on call_events.direction
 *   2. AI Quality tiles
 *   3. Intent Distribution bar chart — clickable → call drill-down modal
 *   4. Consent Distribution bar chart
 *   5. Intent → Outcome table
 *   6. Trend charts
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, CartesianGrid, Legend,
} from "recharts";
import { fetchMetrics, fetchAiTimeSeries, fetchIntentCalls } from "@/lib/api";
import type { MetricsResponse, AiTimeSeriesResponse, IntentCallRow } from "@/types";
import DateRangePicker from "@/components/voice/DateRangePicker";

// Campaign — business concept: New Lead, Cold Lead, or Inbound (new inbound callers)
const CAMPAIGNS: { label: string; value: string | null }[] = [
  { label: "All",       value: null },
  { label: "New Lead",  value: "New Lead" },
  { label: "Cold Lead", value: "Cold Lead" },
  { label: "Inbound",   value: "Inbound" },
];

// Voice Agent — which Synthflow agent handled the call (stored in call_events.voice_agent)
const VOICE_AGENTS: { label: string; value: string | null }[] = [
  { label: "All",      value: null },
  { label: "NewLead",  value: "NewLead" },
  { label: "ColdLead", value: "ColdLead" },
  { label: "Inbound",  value: "Inbound" },
];

// Call direction — from call_events.direction
const DIRECTIONS: { label: string; value: string | null }[] = [
  { label: "All",      value: null },
  { label: "Outbound", value: "Outbound" },
  { label: "Inbound",  value: "Inbound" },
];

function defaultDates() {
  const to = new Date();
  const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago",
  }) + " CST";
}

// ── Intent color map ──────────────────────────────────────────────────────────

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

const BOOKING_INTENTS = new Set(["enrolled"]);

const AXIS_STYLE = { fill: "#64748b", fontSize: 11 };
const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  color: "#1e293b",
  boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
  fontSize: "0.8rem",
};

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

// ── Filter button group ───────────────────────────────────────────────────────

function FilterGroup<T extends string | null>({
  label,
  options,
  active,
  onChange,
  accent,
}: {
  label: string;
  options: { label: string; value: T }[];
  active: T;
  onChange: (v: T) => void;
  accent: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </span>
      <div style={{ display: "flex", gap: "0.2rem" }}>
        {options.map((opt) => {
          const isActive = active === opt.value;
          return (
            <button
              key={opt.label}
              onClick={() => onChange(opt.value)}
              style={{
                padding: "0.3rem 0.75rem",
                border: `1px solid ${isActive ? accent : "#e2e8f0"}`,
                borderRadius: 6,
                fontSize: "0.8rem",
                fontWeight: 600,
                cursor: "pointer",
                background: isActive ? accent : "#fff",
                color: isActive ? "#fff" : "#64748b",
                transition: "all 0.12s",
              }}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── AI Quality tile ───────────────────────────────────────────────────────────

function QualityTile({ label, value, accent, sub }: {
  label: string; value: string; accent: string; sub?: string;
}) {
  return (
    <div style={{ ...CARD, borderTop: `2px solid ${accent}`, padding: "0.875rem 1rem", flex: "1 1 160px" }}>
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
              <th key={h} style={{
                padding: "0.5rem 0.75rem",
                textAlign: h === "Intent" ? "left" : "right",
                fontWeight: 700, color: "#64748b",
                fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.05em",
              }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.intent} style={{ borderBottom: "1px solid #f1f5f9", background: i % 2 === 0 ? "#ffffff" : "#fafafa" }}>
              <td style={{ padding: "0.45rem 0.75rem", color: "#1e293b" }}>
                <span style={{
                  display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                  background: INTENT_COLORS[row.intent] ?? "#3b82f6", marginRight: 8, verticalAlign: "middle",
                }} />
                {row.intent.replace(/_/g, " ")}
              </td>
              <td style={{ padding: "0.45rem 0.75rem", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                {row.count.toLocaleString()}
              </td>
              <td style={{ padding: "0.45rem 0.75rem", textAlign: "right", color: "#475569" }}>
                {row.share}%
              </td>
              <td style={{
                padding: "0.45rem 0.75rem", textAlign: "right", fontWeight: 600,
                color: BOOKING_INTENTS.has(row.intent) ? "#16a34a" : "#94a3b8",
              }}>
                {row.bookingRate}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Intent Drill-Down Modal ───────────────────────────────────────────────────

function IntentDrillDownModal({
  intent, calls, loading, onClose,
}: {
  intent: string;
  calls: IntentCallRow[];
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 50,
        background: "rgba(0,0,0,0.45)",
        display: "flex", alignItems: "flex-start", justifyContent: "center",
        padding: "2rem 1rem", overflowY: "auto",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        background: "#fff", borderRadius: 10, width: "100%", maxWidth: 900,
        boxShadow: "0 8px 40px rgba(0,0,0,0.18)", overflow: "hidden",
      }}>
        <div style={{
          display: "flex", alignItems: "center", gap: "0.75rem",
          padding: "0.875rem 1.25rem",
          background: "#f8fafc", borderBottom: "1px solid #e2e8f0",
        }}>
          <span style={{ fontWeight: 700, fontSize: "0.95rem", color: "#1e293b" }}>
            Calls with intent:{" "}
            <span style={{
              display: "inline-block", padding: "0.1rem 0.45rem", borderRadius: 4,
              background: (INTENT_COLORS[intent] ?? "#3b82f6") + "18",
              color: INTENT_COLORS[intent] ?? "#3b82f6", fontFamily: "monospace",
            }}>
              {intent.replace(/_/g, " ")}
            </span>
          </span>
          {!loading && <span style={{ fontSize: "0.8rem", color: "#64748b" }}>({calls.length} calls)</span>}
          <button
            onClick={onClose}
            style={{
              marginLeft: "auto", background: "none", border: "none",
              fontSize: "1.2rem", color: "#94a3b8", cursor: "pointer",
              lineHeight: 1, padding: "0 0.25rem",
            }}
          >
            ×
          </button>
        </div>

        <div style={{ padding: "1rem 1.25rem", maxHeight: "70vh", overflowY: "auto" }}>
          {loading ? (
            <div style={{ textAlign: "center", padding: "2rem", color: "#64748b" }}>Loading calls…</div>
          ) : calls.length === 0 ? (
            <div style={{ textAlign: "center", padding: "2rem", color: "#94a3b8" }}>No calls found for this intent.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.875rem" }}>
              {calls.map((call, i) => (
                <div key={i} style={{
                  border: "1px solid #e2e8f0", borderRadius: 8,
                  overflow: "hidden", background: i % 2 === 0 ? "#fff" : "#fafafa",
                }}>
                  <div style={{
                    display: "flex", flexWrap: "wrap", gap: "1rem",
                    padding: "0.6rem 0.875rem",
                    background: "#f8fafc", borderBottom: "1px solid #e2e8f0",
                    fontSize: "0.8rem",
                  }}>
                    <span style={{ fontFamily: "monospace", color: "#2563eb", fontWeight: 600 }}>
                      {call.phone}
                    </span>
                    <span style={{ color: "#475569" }}>{fmtTs(call.call_time)}</span>
                    <span style={{ color: "#64748b" }}>{call.duration_seconds}s</span>
                    <span style={{
                      display: "inline-block", padding: "0.1rem 0.4rem", borderRadius: 4,
                      background: "#e2e8f0", color: "#475569", fontSize: "0.72rem",
                    }}>
                      {call.campaign_name}
                    </span>
                    {call.recording_url && (
                      <a
                        href={call.recording_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "#7c3aed", textDecoration: "none", fontWeight: 600 }}
                      >
                        ▶ Recording
                      </a>
                    )}
                  </div>
                  {call.transcript && (
                    <div style={{
                      padding: "0.5rem 0.875rem",
                      fontSize: "0.78rem", color: "#475569", lineHeight: 1.55,
                      maxHeight: 120, overflowY: "auto",
                      whiteSpace: "pre-wrap",
                    }}>
                      {call.transcript.slice(0, 600)}{call.transcript.length > 600 ? "…" : ""}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function EngagementAnalysisPage() {
  const defaults = defaultDates();
  const [fromDate, setFromDate] = useState(defaults.from);
  const [toDate, setToDate] = useState(defaults.to);
  const [activeCampaign, setActiveCampaign] = useState<string | null>(null);
  const [activeVoiceAgent, setActiveVoiceAgent] = useState<string | null>(null);
  const [activeDirection, setActiveDirection] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [timeSeries, setTimeSeries] = useState<AiTimeSeriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Drill-down modal state
  const [drillIntent, setDrillIntent] = useState<string | null>(null);
  const [drillCalls, setDrillCalls] = useState<IntentCallRow[]>([]);
  const [drillLoading, setDrillLoading] = useState(false);

  const load = useCallback(async (
    from: string, to: string,
    campaign: string | null, voiceAgent: string | null, direction: string | null,
  ) => {
    setLoading(true);
    setError(null);
    try {
      const [m, ts] = await Promise.all([
        fetchMetrics({
          campaign: campaign ?? undefined,
          direction: direction ?? undefined,
          voice_agent: voiceAgent ?? undefined,
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

  useEffect(() => {
    load(fromDate, toDate, activeCampaign, activeVoiceAgent, activeDirection);
  }, [fromDate, toDate, activeCampaign, activeVoiceAgent, activeDirection, load]);

  async function handleIntentClick(intentRaw: string) {
    const intent = intentRaw.replace(/ /g, "_");
    setDrillIntent(intent);
    setDrillCalls([]);
    setDrillLoading(true);
    try {
      const result = await fetchIntentCalls({
        intent,
        campaign: activeCampaign ?? undefined,
        voice_agent: activeVoiceAgent ?? undefined,
        direction: activeDirection ?? undefined,
        from_date: fromDate ? `${fromDate}T00:00:00Z` : undefined,
        to_date: toDate ? `${toDate}T23:59:59Z` : undefined,
      });
      setDrillCalls(result.calls);
    } finally {
      setDrillLoading(false);
    }
  }

  // ── Derived values ────────────────────────────────────────────────────────
  const intentDist = metrics?.ai.intent_distribution ?? {};
  const consentDist = metrics?.ai.consent_distribution ?? {};
  const totalIntents = Object.values(intentDist).reduce((s, v) => s + v, 0);
  const totalCalls = metrics?.kpis.total_calls ?? 0;
  const blankRate = metrics?.ai.blank_transcript_rate;
  const unknownCount = intentDist["low_confidence_audio"] ?? 0;
  const unknownRate = totalIntents > 0 ? ((unknownCount / totalIntents) * 100).toFixed(1) : null;

  const intentChartData = Object.entries(intentDist)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({ name: name.replace(/_/g, " "), rawName: name, count }));

  const consentChartData = Object.entries(consentDist).map(([name, count]) => ({ name, count }));

  const trendData = (timeSeries?.time_series ?? []).map((pt) => ({
    date: pt.date,
    "Blank Transcript %": pt.blank_transcript_rate,
    "Unknown Intent %": pt.unknown_intent_rate,
  }));

  return (
    <div style={{ minHeight: "100vh", background: "#f1f5f9", fontFamily: "system-ui, -apple-system, sans-serif", color: "#1e293b" }}>

      {/* ── Topbar ─────────────────────────────────────────────────────────── */}
      <div style={{
        position: "sticky", top: 0, zIndex: 10, height: 48,
        background: "#ffffff", borderBottom: "1px solid #e2e8f0",
        display: "flex", alignItems: "center", padding: "0 1.5rem",
        gap: "0.875rem", boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
      }}>
        <Link href="/" style={{ color: "#94a3b8", textDecoration: "none", fontSize: "0.875rem", fontWeight: 500 }}>
          ← Dashboard
        </Link>
        <span style={{ width: 1, height: 16, background: "#e2e8f0" }} />
        <span style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b" }}>Engagement Analysis</span>
        {loading && <span style={{ fontSize: "0.75rem", color: "#94a3b8", marginLeft: 4 }}>Refreshing…</span>}
        {error && <span style={{ fontSize: "0.75rem", color: "#ef4444", marginLeft: 4 }}>⚠ {error}</span>}
        <div style={{ marginLeft: "auto" }}>
          <DateRangePicker fromDate={fromDate} toDate={toDate} onFromChange={setFromDate} onToChange={setToDate} />
        </div>
      </div>

      {/* ── Content ────────────────────────────────────────────────────────── */}
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "1.5rem 1.5rem 3rem" }}>

        {/* Page heading */}
        <div style={{ marginBottom: "1rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.35rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.025em" }}>
            Engagement Analysis
          </h1>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
            Intent detection, consent analysis & engagement quality metrics.
            {metrics && (
              <span style={{ color: "#94a3b8", marginLeft: 8 }}>
                {metrics.period.from.slice(0, 10)} → {metrics.period.to.slice(0, 10)}
              </span>
            )}
          </p>
        </div>

        {/* ── Filter bar ───────────────────────────────────────────────────── */}
        <div style={{
          ...CARD,
          padding: "0.875rem 1rem",
          marginBottom: "1.25rem",
          display: "flex",
          gap: "1.5rem",
          alignItems: "flex-end",
          flexWrap: "wrap",
        }}>
          <FilterGroup
            label="Campaign"
            options={CAMPAIGNS}
            active={activeCampaign}
            onChange={setActiveCampaign}
            accent="#3b82f6"
          />
          <div style={{ width: 1, height: 36, background: "#e2e8f0", alignSelf: "flex-end" }} />
          <FilterGroup
            label="Voice Agent"
            options={VOICE_AGENTS}
            active={activeVoiceAgent}
            onChange={setActiveVoiceAgent}
            accent="#0891b2"
          />
          <div style={{ width: 1, height: 36, background: "#e2e8f0", alignSelf: "flex-end" }} />
          <FilterGroup
            label="Call Direction"
            options={DIRECTIONS}
            active={activeDirection}
            onChange={setActiveDirection}
            accent="#7c3aed"
          />
          {(activeCampaign || activeVoiceAgent || activeDirection) && (
            <button
              onClick={() => { setActiveCampaign(null); setActiveVoiceAgent(null); setActiveDirection(null); }}
              style={{
                padding: "0.3rem 0.75rem", border: "1px solid #e2e8f0",
                borderRadius: 6, fontSize: "0.78rem", fontWeight: 500,
                cursor: "pointer", background: "#f8fafc", color: "#64748b",
                alignSelf: "flex-end",
              }}
            >
              Clear filters
            </button>
          )}
          <span style={{ marginLeft: "auto", fontSize: "0.78rem", color: "#94a3b8", alignSelf: "flex-end" }}>
            {!loading && `${totalCalls.toLocaleString()} calls`}
          </span>
        </div>

        {/* ── AI Quality tiles ─────────────────────────────────────────────── */}
        <div style={{ ...SECTION_LABEL }}>Engagement Quality Metrics</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.875rem", marginBottom: "1.5rem" }}>
          <QualityTile label="Calls Analyzed" value={totalCalls.toLocaleString()} accent="#3b82f6" sub="call events in period" />
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
          <QualityTile label="Intents Classified" value={totalIntents.toLocaleString()} accent="#7c3aed" sub="calls with a detected intent" />
        </div>

        {/* ── Intent + Consent side-by-side ────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: "1rem", marginBottom: "1.5rem" }}>

          {/* Intent Distribution — clickable bars */}
          <div style={{ ...CARD, padding: "1.25rem" }}>
            <div style={SECTION_LABEL}>
              Intent Distribution
              <span style={{ fontWeight: 400, color: "#94a3b8", marginLeft: 6, textTransform: "none", letterSpacing: 0 }}>
                — click a bar to see calls
              </span>
            </div>
            {intentChartData.length === 0 ? (
              <p style={{ color: "#94a3b8", fontSize: "0.85rem" }}>No data for this period.</p>
            ) : (
              <ResponsiveContainer width="100%" height={Math.max(280, intentChartData.length * 22)}>
                <BarChart
                  data={intentChartData}
                  layout="vertical"
                  margin={{ top: 4, right: 40, left: 8, bottom: 4 }}
                  style={{ cursor: "pointer" }}
                >
                  <XAxis type="number" stroke="#e2e8f0" tick={AXIS_STYLE} />
                  <YAxis type="category" dataKey="name" width={165} stroke="#e2e8f0" tick={AXIS_STYLE} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#0000000a" }} />
                  <Bar
                    dataKey="count"
                    radius={[0, 4, 4, 0]}
                    onClick={(data) => handleIntentClick(data.name as string)}
                  >
                    {intentChartData.map((entry) => (
                      <Cell key={entry.name} fill={INTENT_COLORS[entry.rawName] ?? "#3b82f6"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Consent Distribution */}
          <div style={{ ...CARD, padding: "1.25rem" }}>
            <div style={SECTION_LABEL}>Consent Distribution</div>
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

        {/* ── Intent → Outcome table ────────────────────────────────────────── */}
        <div style={{ ...CARD, marginBottom: "1.5rem", overflow: "hidden" }}>
          <div style={{ padding: "1.25rem 1.25rem 0.75rem", ...SECTION_LABEL }}>
            Intent → Outcome Mapping
          </div>
          <IntentOutcomeTable intentDist={intentDist} />
        </div>

        {/* ── Trend charts ─────────────────────────────────────────────────── */}
        <div style={{ ...CARD, padding: "1.25rem" }}>
          <div style={SECTION_LABEL}>
            AI Error & Quality Trends
            <span style={{ fontWeight: 400, color: "#94a3b8", marginLeft: 8, textTransform: "none", letterSpacing: 0 }}>
              — weekly buckets
            </span>
          </div>
          {trendData.length === 0 ? (
            <div style={{ height: 200, display: "flex", alignItems: "center", justifyContent: "center", color: "#94a3b8", fontSize: "0.85rem" }}>
              No trend data for this period. Try a longer date range.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trendData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="#e2e8f0" tick={{ fill: "#64748b", fontSize: 11 }} />
                <YAxis stroke="#e2e8f0" tick={{ fill: "#64748b", fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
                <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${v !== null ? v.toFixed(1) : "—"}%`]} />
                <Legend wrapperStyle={{ fontSize: "0.78rem", color: "#475569" }} />
                <Line type="monotone" dataKey="Blank Transcript %" stroke="#d97706" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="Unknown Intent %" stroke="#94a3b8" strokeWidth={2} dot={false} strokeDasharray="5 3" />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

      </div>

      {/* ── Intent Drill-Down Modal ───────────────────────────────────────── */}
      {drillIntent && (
        <IntentDrillDownModal
          intent={drillIntent}
          calls={drillCalls}
          loading={drillLoading}
          onClose={() => { setDrillIntent(null); setDrillCalls([]); }}
        />
      )}
    </div>
  );
}
