"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchCampaignOverview } from "@/lib/api";
import type { CampaignOverviewRow } from "@/types";

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function addDays(iso: string, n: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  }) + " UTC";
}

const STATUS_COLORS: Record<string, string> = {
  "Do Not Call": "#ef4444",
  "Invalid": "#f97316",
  "Enrolled": "#22c55e",
  "Closed": "#64748b",
};

const ACTION_COLOR: Record<string, string> = {
  Call: "#3b82f6",
  SMS: "#8b5cf6",
  Email: "#ec4899",
  "Follow-up": "#06b6d4",
};

function actionColor(next: string | null): string {
  if (!next) return "#94a3b8";
  for (const [k, c] of Object.entries(ACTION_COLOR)) {
    if (next.startsWith(k)) return c;
  }
  return "#94a3b8";
}

const CELL: React.CSSProperties = {
  padding: "0.55rem 0.875rem",
  borderBottom: "1px solid #f1f5f9",
  fontSize: "0.82rem",
  color: "#334155",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 240,
};

const HEAD: React.CSSProperties = {
  padding: "0.55rem 0.875rem",
  background: "#f8fafc",
  borderBottom: "2px solid #e2e8f0",
  fontSize: "0.75rem",
  fontWeight: 700,
  color: "#64748b",
  textTransform: "uppercase" as const,
  letterSpacing: "0.06em",
  whiteSpace: "nowrap",
};

export default function CampaignOverviewClient() {
  const today = todayIso();
  const [fromDate, setFromDate] = useState(today);
  const [toDate, setToDate] = useState(addDays(today, 7));
  const [rows, setRows] = useState<CampaignOverviewRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [campaignFilter, setCampaignFilter] = useState<string>("All");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchCampaignOverview({ from_date: fromDate, to_date: toDate });
      setRows(data.rows);
      setTotal(data.total);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate]);

  useEffect(() => { load(); }, [load]);

  const campaigns = ["All", ...Array.from(new Set(rows.map((r) => r.campaign_name).filter(Boolean)))];

  const visible = campaignFilter === "All"
    ? rows
    : rows.filter((r) => r.campaign_name === campaignFilter);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      {/* ── Controls ── */}
      <div
        style={{
          display: "flex",
          gap: "0.75rem",
          alignItems: "flex-end",
          flexWrap: "wrap",
          background: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "0.875rem 1rem",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>
            Next action from
          </label>
          <input
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            style={{
              border: "1px solid #e2e8f0", borderRadius: 6,
              padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b",
              background: "#fff",
            }}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>
            Next action to
          </label>
          <input
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            style={{
              border: "1px solid #e2e8f0", borderRadius: 6,
              padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b",
              background: "#fff",
            }}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>
            Campaign
          </label>
          <select
            value={campaignFilter}
            onChange={(e) => setCampaignFilter(e.target.value)}
            style={{
              border: "1px solid #e2e8f0", borderRadius: 6,
              padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b",
              background: "#fff",
            }}
          >
            {campaigns.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>

        <button
          onClick={load}
          disabled={loading}
          style={{
            padding: "0.4rem 1rem",
            background: "#3b82f6",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            fontSize: "0.85rem",
            fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer",
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>

        <span style={{ marginLeft: "auto", fontSize: "0.8rem", color: "#64748b", alignSelf: "center" }}>
          {loading ? "" : `${visible.length} contact${visible.length !== 1 ? "s" : ""} · ${fromDate} → ${toDate}`}
        </span>
      </div>

      {/* ── Error ── */}
      {error && (
        <div style={{
          background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8,
          padding: "0.75rem 1rem", color: "#991b1b", fontSize: "0.85rem",
        }}>
          {error}
        </div>
      )}

      {/* ── Table ── */}
      {!loading && !error && visible.length === 0 ? (
        <div style={{
          background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "2.5rem", textAlign: "center", color: "#64748b", fontSize: "0.9rem",
        }}>
          No contacts with scheduled activity in this window.
        </div>
      ) : (
        <div style={{
          background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
          overflow: "hidden",
        }}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["Contact", "Campaign", "Last Call", "Next Action", "Status"].map((h) => (
                    <th key={h} style={HEAD}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((row, i) => (
                  <tr
                    key={row.contact_id + i}
                    style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}
                  >
                    <td style={{ ...CELL, fontFamily: "monospace", fontSize: "0.8rem", color: "#0f172a" }}>
                      {row.contact}
                    </td>
                    <td style={CELL}>{row.campaign_name}</td>
                    <td style={{ ...CELL, color: "#475569" }}>{fmtTs(row.last_call_at)}</td>
                    <td style={CELL}>
                      {row.next_action ? (
                        <span style={{
                          display: "inline-block",
                          padding: "0.15rem 0.5rem",
                          borderRadius: 4,
                          background: actionColor(row.next_action) + "18",
                          color: actionColor(row.next_action),
                          fontWeight: 600,
                          fontSize: "0.78rem",
                        }}>
                          {row.next_action}
                        </span>
                      ) : "—"}
                    </td>
                    <td style={CELL}>
                      {row.status ? (
                        <span style={{
                          display: "inline-block",
                          padding: "0.15rem 0.5rem",
                          borderRadius: 4,
                          background: (STATUS_COLORS[row.status] ?? "#94a3b8") + "18",
                          color: STATUS_COLORS[row.status] ?? "#64748b",
                          fontWeight: 600,
                          fontSize: "0.78rem",
                        }}>
                          {row.status}
                        </span>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
