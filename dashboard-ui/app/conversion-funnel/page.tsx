"use client";

/**
 * Recent Calls — /conversion-funnel (path kept for routing stability)
 *
 * Shows calls with duration >= 30s that have transcript and recording.
 * Ordered by call time descending.
 * Filters: date range, voice agent (campaign).
 * Columns: Date/Time (CST), Phone, Duration, Recording, Campaign, Status, Intent.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchRecentCalls } from "@/lib/api";
import type { RecentCallRow } from "@/types";

const VOICE_AGENTS = [
  { label: "All", campaign: "" },
  { label: "NewLead",  campaign: "New Lead" },
  { label: "ColdLead", campaign: "Cold Lead" },
  { label: "Inbound",  campaign: "Inbound" },
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

function fmtDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

const CARD: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
};

const HEAD: React.CSSProperties = {
  padding: "0.5rem 0.875rem",
  background: "#f8fafc",
  borderBottom: "2px solid #e2e8f0",
  fontSize: "0.72rem",
  fontWeight: 700,
  color: "#64748b",
  textTransform: "uppercase" as const,
  letterSpacing: "0.06em",
  whiteSpace: "nowrap",
};

const CELL: React.CSSProperties = {
  padding: "0.5rem 0.875rem",
  borderBottom: "1px solid #f1f5f9",
  fontSize: "0.8rem",
  color: "#334155",
  verticalAlign: "top",
};

// ── Expanded transcript row ───────────────────────────────────────────────────

function CallRow({ call, index }: { call: RecentCallRow; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const bg = index % 2 === 0 ? "#fff" : "#fafafa";

  return (
    <>
      <tr style={{ background: bg }}>
        <td style={{ ...CELL, whiteSpace: "nowrap", color: "#475569" }}>
          {fmtTs(call.call_time)}
        </td>
        <td style={{ ...CELL }}>
          <span style={{ fontFamily: "monospace", fontSize: "0.78rem", color: "#1e293b" }}>
            {call.phone}
          </span>
        </td>
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>{fmtDuration(call.duration_seconds)}</td>
        <td style={{ ...CELL }}>
          {call.recording_url ? (
            <a
              href={call.recording_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#7c3aed", textDecoration: "none", fontWeight: 600, fontSize: "0.78rem" }}
            >
              ▶ Play
            </a>
          ) : (
            <span style={{ color: "#cbd5e1", fontSize: "0.78rem" }}>—</span>
          )}
        </td>
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>
          <span style={{
            display: "inline-block", padding: "0.1rem 0.4rem", borderRadius: 4,
            background: "#e2e8f0", color: "#475569", fontSize: "0.72rem", fontWeight: 600,
          }}>
            {call.campaign_name}
          </span>
        </td>
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>
          {call.status && (
            <span style={{
              display: "inline-block", padding: "0.1rem 0.4rem", borderRadius: 4,
              background: call.status === "completed" ? "#16a34a18" : "#e2e8f0",
              color: call.status === "completed" ? "#16a34a" : "#64748b",
              fontSize: "0.72rem", fontWeight: 600,
            }}>
              {call.status}
            </span>
          )}
        </td>
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>
          {call.detected_intent ? (
            <span style={{ fontSize: "0.75rem", color: "#475569" }}>
              {call.detected_intent.replace(/_/g, " ")}
            </span>
          ) : "—"}
        </td>
        <td style={{ ...CELL }}>
          {call.transcript && (
            <button
              onClick={() => setExpanded((v) => !v)}
              style={{
                background: "none", border: "none", padding: 0,
                color: "#2563eb", fontSize: "0.75rem", cursor: "pointer",
                textDecoration: "underline", textDecorationStyle: "dotted",
              }}
            >
              {expanded ? "Hide" : "Show"} transcript
            </button>
          )}
        </td>
      </tr>
      {expanded && call.transcript && (
        <tr style={{ background: bg }}>
          <td colSpan={8} style={{ padding: "0.5rem 0.875rem 0.75rem", borderBottom: "1px solid #f1f5f9" }}>
            <div style={{
              background: "#f8fafc", border: "1px solid #e2e8f0",
              borderRadius: 6, padding: "0.625rem 0.75rem",
              fontSize: "0.78rem", color: "#334155", lineHeight: 1.6,
              whiteSpace: "pre-wrap", maxHeight: 220, overflowY: "auto",
            }}>
              {call.transcript}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RecentCallsPage() {
  const defaults = defaultDates();
  const [fromDate, setFromDate] = useState(defaults.from);
  const [toDate, setToDate] = useState(defaults.to);
  const [agentIdx, setAgentIdx] = useState(0);
  const [calls, setCalls] = useState<RecentCallRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const activeCampaign = VOICE_AGENTS[agentIdx]?.campaign || undefined;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRecentCalls({
        from_date: fromDate ? `${fromDate}T00:00:00Z` : undefined,
        to_date: toDate ? `${toDate}T23:59:59Z` : undefined,
        campaign: activeCampaign,
      });
      setCalls(data.calls);
      setTotal(data.total);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, activeCampaign]);

  useEffect(() => { load(); }, [load]);

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
        <span style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b" }}>Recent Calls</span>
        {loading && <span style={{ fontSize: "0.75rem", color: "#94a3b8", marginLeft: 4 }}>Loading…</span>}
        {error && <span style={{ fontSize: "0.75rem", color: "#ef4444", marginLeft: 4 }}>⚠ {error}</span>}
      </div>

      {/* ── Content ────────────────────────────────────────────────────────── */}
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "1.5rem 1.5rem 3rem" }}>

        {/* Page heading */}
        <div style={{ marginBottom: "1.25rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.35rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.025em" }}>
            Recent Calls
          </h1>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
            Calls lasting 30 seconds or more with transcript and recording. Times shown in CST.
          </p>
        </div>

        {/* ── Filters ────────────────────────────────────────────────────────── */}
        <div style={{
          ...CARD, padding: "0.875rem 1rem", marginBottom: "1rem",
          display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap",
        }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748b" }}>From</label>
            <input
              type="date" value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }}
            />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748b" }}>To</label>
            <input
              type="date" value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }}
            />
          </div>

          {/* Voice Agent tab buttons */}
          <div style={{ display: "flex", gap: "0.25rem", alignSelf: "flex-end" }}>
            {VOICE_AGENTS.map((a, idx) => (
              <button
                key={a.label}
                onClick={() => setAgentIdx(idx)}
                style={{
                  padding: "0.35rem 0.75rem", border: "1px solid #e2e8f0",
                  borderRadius: 6, fontSize: "0.8rem", fontWeight: 600,
                  cursor: "pointer",
                  background: agentIdx === idx ? "#1e293b" : "#fff",
                  color: agentIdx === idx ? "#fff" : "#64748b",
                }}
              >
                {a.label}
              </button>
            ))}
          </div>

          <button
            onClick={load} disabled={loading}
            style={{
              padding: "0.4rem 1rem", background: "#3b82f6", color: "#fff",
              border: "none", borderRadius: 6, fontSize: "0.85rem", fontWeight: 600,
              cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1,
              alignSelf: "flex-end",
            }}
          >
            {loading ? "Loading…" : "Refresh"}
          </button>

          <span style={{ marginLeft: "auto", fontSize: "0.8rem", color: "#64748b", alignSelf: "center" }}>
            {!loading && `${total} call${total !== 1 ? "s" : ""}`}
          </span>
        </div>

        {/* ── Table ────────────────────────────────────────────────────────────── */}
        {error ? (
          <div style={{ ...CARD, padding: "1rem", color: "#991b1b", background: "#fef2f2", border: "1px solid #fecaca" }}>
            {error}
          </div>
        ) : !loading && calls.length === 0 ? (
          <div style={{ ...CARD, padding: "3rem", textAlign: "center", color: "#64748b", fontSize: "0.9rem" }}>
            No calls matching the current filters. Try expanding the date range.
          </div>
        ) : (
          <div style={{ ...CARD, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {["Date / Time (CST)", "Phone", "Duration", "Recording", "Campaign", "Status", "Intent", "Transcript"].map((h) => (
                      <th key={h} style={HEAD}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {calls.map((call, i) => (
                    <CallRow key={`${call.contact_id}-${i}`} call={call} index={i} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
