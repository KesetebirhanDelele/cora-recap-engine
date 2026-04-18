"use client";

/**
 * Voice Call Performance Review — /voice-performance
 *
 * Single-screen layout (no vertical scroll):
 *   ┌─────────────┬──────────────────────────────────────────┐
 *   │             │  Header + Date Range                     │
 *   │  KPI        ├──────────────────────────────────────────┤
 *   │  Sidebar    │  Trends Over Time (flex-grow)            │
 *   │             ├────────────────────┬─────────────────────┤
 *   │             │  WoW % Performance │  Are We Wasting?    │
 *   └─────────────┴────────────────────┴─────────────────────┘
 *
 * Key trick: `height: 100vh; overflow: hidden` on root, then every child
 * uses flex/minHeight:0 so ResponsiveContainer height="100%" resolves.
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

function defaultDates() {
  const to = new Date();
  // Align "from" to the Monday of the week that is 7 weeks before the current week,
  // so the default window is 8 calendar weeks (7 past + current, inclusive).
  const dayOfWeek = to.getDay(); // 0 = Sun, 1 = Mon, …
  const daysToMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
  const startOfCurrentWeek = new Date(to);
  startOfCurrentWeek.setDate(to.getDate() - daysToMonday);
  const from = new Date(startOfCurrentWeek.getTime() - 7 * 7 * 24 * 60 * 60 * 1000);
  return {
    from: from.toISOString().slice(0, 10),
    to: to.toISOString().slice(0, 10),
  };
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

export default function VoicePerformancePage() {
  const defaults = defaultDates();
  const [fromDate, setFromDate] = useState(defaults.from);
  const [toDate, setToDate] = useState(defaults.to);
  // Filtered data — drives Trends Over Time and WoW waterfall
  const [data, setData] = useState<VoicePerformanceResponse | null>(null);
  // Unfiltered (all-time) data — drives KPI sidebar and bubble chart
  const [allData, setAllData] = useState<VoicePerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (from: string, to: string) => {
    setLoading(true);
    setError(null);
    try {
      const [filtered, all] = await Promise.all([
        fetchVoicePerformance({
          from_date: from ? `${from}T00:00:00Z` : undefined,
          to_date:   to   ? `${to}T23:59:59Z`   : undefined,
        }),
        fetchVoicePerformance({ all_time: true }), // no date filter — cumulative totals
      ]);
      setData(filtered);
      setAllData(all);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(fromDate, toDate); }, [fromDate, toDate, load]);

  // KPI sidebar and bubble chart always use cumulative (all-time) data
  const kpis              = allData?.kpis               ?? EMPTY_KPIS;
  const campaignBreakdown = allData?.campaign_breakdown ?? [];
  // Trends and WoW waterfall use the date-filtered data
  const timeSeries        = data?.time_series           ?? [];
  const wowChanges        = data?.wow_changes           ?? {};

  return (
    /*
     * Root: full viewport, no scroll.
     * flex-column: topbar (fixed) + main (fills rest).
     */
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
      {/* ── Top bar (40px) ──────────────────────────────────────────────── */}
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
        <Link
          href="/"
          style={{ color: "#64748b", textDecoration: "none", fontSize: "0.9rem" }}
        >
          ← Dashboard
        </Link>
        <span style={{ color: "#e2e8f0" }}>|</span>
        <span style={{ fontSize: "1rem", fontWeight: 600, color: "#1e293b" }}>
          Voice Call Performance Review
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

      {/*
       * ── Main area ───────────────────────────────────────────────────────
       * flex-row: sidebar (240px) + right content (flex:1)
       * padding + gap to keep cards separated
       */}
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
        {/* ── KPI Sidebar ─────────────────────────────────────────────── */}
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
            All-time · cumulative
          </div>
          <KpiSidebar kpis={kpis} wowChanges={wowChanges} />
        </div>

        {/*
         * ── Right column ────────────────────────────────────────────────
         * flex-column: header → trends (flex 2) → bottom row (flex 1)
         */}
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

          {/* Trends Over Time — takes 2/3 of remaining vertical space */}
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
                — stacked calls by voice agent + rate lines
              </span>
            </div>
            {/* Chart fills remaining height via flex: 1 + minHeight: 0 */}
            <div style={{ flex: 1, minHeight: 0 }}>
              <TrendsChart data={timeSeries} height="100%" />
            </div>
          </div>

          {/* Bottom row — takes 1/3 of remaining vertical space */}
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
              </div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <WowWaterfall wowChanges={wowChanges} height="100%" />
              </div>
            </div>

            {/* Are we wasting calls? */}
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
                  — pickup vs booking %, bubble = calls · all-time
                </span>
              </div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <EfficiencyScatter data={campaignBreakdown} height="100%" />
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
