"use client";

/**
 * Voice Call Performance Review v2 — /voice-performance-v2
 *
 * Differences from v1:
 *   - Date filter controls ALL visuals (KPI sidebar, bubble chart, WoW waterfall)
 *   - Default range: Monday of earliest week with data → today
 *   - TrendsChart + EfficiencyScatter sub-filtered to the 8 most recent calendar
 *     weeks counting back from the week that contains the page filter end date
 *   - KPI sidebar aggregates over the selected date range
 *   - WoW waterfall compares the calendar week of the end date vs the week before
 */

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { fetchVoicePerformance } from "@/lib/api";
import type { VoicePerformanceResponse } from "@/types";
import KpiSidebar from "@/components/voice/KpiSidebar";
import TrendsChart from "@/components/voice/TrendsChart";
import WowWaterfall from "@/components/voice/WowWaterfall";
import EfficiencyScatter from "@/components/voice/EfficiencyScatter";
import DateRangePicker from "@/components/voice/DateRangePicker";

/** Monday of the week containing d (ISO date string). */
function mondayOfWeek(d: Date): string {
  const copy = new Date(d);
  const dow = copy.getDay();
  copy.setDate(copy.getDate() - (dow === 0 ? 6 : dow - 1));
  return copy.toISOString().slice(0, 10);
}

/** Monday n full weeks before the Monday of the week containing d. */
function mondayNWeeksBack(d: Date, n: number): string {
  const copy = new Date(d);
  const dow = copy.getDay();
  copy.setDate(copy.getDate() - (dow === 0 ? 6 : dow - 1) - n * 7);
  return copy.toISOString().slice(0, 10);
}

const CARD: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
};

const SECTION_LABEL: React.CSSProperties = {
  fontSize: "0.85rem",
  fontWeight: 700,
  color: "#b45309",
  textTransform: "uppercase",
  letterSpacing: "0.07em",
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  marginBottom: "0.3rem",
};

const EMPTY_KPIS = {
  unique_contacts: 0,
  booked_appts: 0,
  calls_per_day: 0,
  completion_rate: null,
  avg_call_duration_sec: 0,
  pickup_rate: null,
  voicemail_rate: null,
  failed_rate: null,
  total_calls: 0,
  booking_rate: null,
};

export default function VoicePerformanceV2Page() {
  const today = new Date().toISOString().slice(0, 10);
  const [fromDate, setFromDate] = useState("2025-07-16");
  const [toDate, setToDate] = useState(today);
  const [data, setData] = useState<VoicePerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (from: string, to: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchVoicePerformance({
        from_date: from ? `${from}T00:00:00Z` : undefined,
        to_date:   to   ? `${to}T23:59:59Z`   : undefined,
        wow_mode: true, // calendar week of to_date vs prior calendar week — matches PowerBI WoW formula
      });
      setData(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(fromDate, toDate); }, [fromDate, toDate, load]);

  // Sub-filter time_series to the 8 most recent calendar weeks from the end date
  const toDateObj = toDate ? new Date(toDate + "T12:00:00Z") : new Date();
  const eightWeekCutoff = mondayNWeeksBack(toDateObj, 7); // Mon of (current week - 7) = 8 weeks total
  const fullTimeSeries    = data?.time_series ?? [];
  const recentTimeSeries  = fullTimeSeries.filter((pt) => pt.date >= eightWeekCutoff);

  // Bubble chart and KPIs all come from the same date-filtered response
  const kpis              = data?.kpis               ?? EMPTY_KPIS;
  const campaignBreakdown = data?.campaign_breakdown ?? [];
  const wowChanges        = data?.wow_changes        ?? {};

  // WoW subtitle: calendar week of to_date vs prior calendar week
  const wowCurrStart = mondayOfWeek(toDateObj);          // Mon of current week
  const wowPrevStart = mondayNWeeksBack(toDateObj, 1);   // Mon of prior week
  const wowPrevEnd   = wowCurrStart;                     // prior week ends at current Mon

return (
    <div
      style={{
        height: "100vh",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        background: "#f1f5f9",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: "#1e293b",
      }}
    >
      {/* ── Top bar ─────────────────────────────────────────────────────────── */}
      <div
        style={{
          height: 40,
          flexShrink: 0,
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          display: "flex",
          alignItems: "center",
          padding: "0 1rem",
          gap: "0.625rem",
        }}
      >
        <Link href="/" style={{ color: "#64748b", textDecoration: "none", fontSize: "0.9rem" }}>
          ← Dashboard
        </Link>
        <span style={{ color: "#e2e8f0" }}>|</span>
        <Link href="/voice-performance" style={{ color: "#64748b", textDecoration: "none", fontSize: "0.9rem" }}>
          v1
        </Link>
        <span style={{ color: "#e2e8f0" }}>|</span>
        <span style={{ fontSize: "1rem", fontWeight: 600, color: "#1e293b" }}>
          Voice Call Performance Review <span style={{ color: "#94a3b8", fontWeight: 400 }}>v2</span>
        </span>
        {loading && (
          <span style={{ marginLeft: "auto", fontSize: "0.875rem", color: "#94a3b8" }}>
            Refreshing…
          </span>
        )}
        {error && (
          <span style={{ marginLeft: "auto", fontSize: "0.875rem", color: "#ef4444" }}>
            ⚠ {error}
          </span>
        )}
      </div>

      {/* ── Main area ───────────────────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          gap: "0.625rem",
          padding: "0.625rem",
          overflow: "hidden",
        }}
      >
        {/* ── KPI Sidebar ─────────────────────────────────────────────────── */}
        <div
          style={{
            width: 236,
            flexShrink: 0,
            ...CARD,
            padding: "0.5rem 0.625rem",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div style={{ fontSize: "0.72rem", color: "#94a3b8", marginBottom: "0.35rem", fontStyle: "italic" }}>
            {fromDate} → {toDate}
          </div>
          <KpiSidebar kpis={kpis} wowChanges={wowChanges} />
        </div>

        {/* ── Right column ────────────────────────────────────────────────── */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            gap: "0.625rem",
            overflow: "hidden",
          }}
        >
          {/* Header */}
          <div
            style={{
              flexShrink: 0,
              ...CARD,
              padding: "0.5rem 0.875rem",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "0.75rem",
              flexWrap: "wrap",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: "1.2rem",
                  fontWeight: 700,
                  color: "#b45309",
                  letterSpacing: "-0.01em",
                  lineHeight: 1.2,
                }}
              >
                Cora Voice AI Agent Performance Overview
              </div>
              {data && (
                <div style={{ fontSize: "0.85rem", color: "#94a3b8", marginTop: 2 }}>
                  {data.period.from.slice(0, 10)} → {data.period.to.slice(0, 10)}
                  {" · "}{data.kpis.total_calls.toLocaleString()} calls
                </div>
              )}
            </div>
            <DateRangePicker
              fromDate={fromDate}
              toDate={toDate}
              onFromChange={setFromDate}
              onToChange={setToDate}
            />
          </div>

          {/* Trends Over Time — 8-week sub-filtered */}
          <div
            style={{
              flex: 2,
              minHeight: 0,
              ...CARD,
              padding: "0.5rem 0.75rem 0.375rem",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            <div style={SECTION_LABEL}>
              <span>📈</span> Trends Over Time
              <span style={{ color: "#cbd5e1", fontWeight: 400, fontSize: "0.78rem" }}>
                — 8 weeks ending {toDate} · stacked calls + rate lines
              </span>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              <TrendsChart data={recentTimeSeries} height="100%" />
            </div>
          </div>

          {/* Bottom row */}
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              gap: "0.625rem",
              overflow: "hidden",
            }}
          >
            {/* WoW % Performance */}
            <div
              style={{
                flex: 1,
                minWidth: 0,
                ...CARD,
                padding: "0.5rem 0.75rem 0.375rem",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              <div style={SECTION_LABEL}>
                <span>📊</span> WoW % Performance
                <span style={{ color: "#cbd5e1", fontWeight: 400, fontSize: "0.78rem" }}>
                  — {wowCurrStart} → {toDate} vs {wowPrevStart} → {wowPrevEnd}
                </span>
              </div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <WowWaterfall wowChanges={wowChanges} height="100%" />
              </div>
            </div>

            {/* Are we wasting calls? — date-filtered */}
            <div
              style={{
                flex: 1,
                minWidth: 0,
                ...CARD,
                padding: "0.5rem 0.75rem 0.375rem",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              <div style={SECTION_LABEL}>
                <span>🎯</span> Are we wasting calls?
                <span style={{ color: "#cbd5e1", fontWeight: 400, fontSize: "0.78rem" }}>
                  — pickup vs booking % · bubble = avg calls/day · {fromDate} → {toDate}
                </span>
              </div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <EfficiencyScatter data={recentTimeSeries.length > 0 ? campaignBreakdown : []} height="100%" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
