"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchCampaignOverview } from "@/lib/api";
import type { CampaignOverviewRow } from "@/types";
import ContactLookupClient from "@/components/ContactLookupClient";

// ── Helpers ───────────────────────────────────────────────────────────────────

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
  return new Date(iso).toLocaleString("en-US", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago",
  }) + " CST";
}

// ── Styles ────────────────────────────────────────────────────────────────────

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

const STATUS_COLORS: Record<string, string> = {
  "Do Not Call": "#ef4444",
  "Invalid":     "#f97316",
  "Enrolled":    "#22c55e",
  "Closed":      "#64748b",
};

const ACTION_COLOR: Record<string, string> = {
  Call: "#3b82f6",
  SMS:  "#8b5cf6",
  Email: "#ec4899",
  "Follow-up": "#06b6d4",
};

const OUTCOME_COLORS: Record<string, string> = {
  booked:         "#22c55e",
  follow_up:      "#3b82f6",
  not_interested: "#64748b",
  no_answer:      "#f59e0b",
  voicemail:      "#8b5cf6",
  wrong_number:   "#ef4444",
};

const OUTCOME_LABELS: Record<string, string> = {
  booked:         "Booked",
  follow_up:      "Follow-Up",
  not_interested: "Not Interested",
  no_answer:      "No Answer",
  voicemail:      "Voicemail",
  wrong_number:   "Wrong Number",
};

function actionColor(next: string | null): string {
  if (!next) return "#94a3b8";
  for (const [k, c] of Object.entries(ACTION_COLOR)) {
    if (next.startsWith(k)) return c;
  }
  return "#94a3b8";
}

function Badge({ val, color }: { val: string; color: string }) {
  return (
    <span style={{
      display: "inline-block", padding: "0.15rem 0.5rem",
      borderRadius: 4, background: color + "18", color,
      fontWeight: 600, fontSize: "0.78rem",
    }}>
      {val}
    </span>
  );
}

// ── Campaign Overview table ───────────────────────────────────────────────────

function OverviewTable({
  rows,
  onDrillDown,
  emptyMessage,
}: {
  rows: CampaignOverviewRow[];
  onDrillDown: (contactId: string, phone: string) => void;
  emptyMessage: string;
}) {
  if (rows.length === 0) {
    return (
      <div style={{
        background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
        padding: "2.5rem", textAlign: "center", color: "#64748b", fontSize: "0.9rem",
      }}>
        {emptyMessage}
      </div>
    );
  }

  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["Phone", "Campaign", "Last Call (CST)", "Next Action", "Outcome", "Status"].map((h) => (
                <th key={h} style={HEAD}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={row.contact_id + i}
                style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}
              >
                <td style={CELL}>
                  <button
                    onClick={() => onDrillDown(row.contact_id, row.contact)}
                    style={{
                      background: "none", border: "none", padding: 0,
                      fontFamily: "monospace", fontSize: "0.8rem",
                      color: "#2563eb", cursor: "pointer",
                      textDecoration: "underline", textDecorationStyle: "dotted",
                    }}
                    title="Click to view full contact details"
                  >
                    {row.contact}
                  </button>
                </td>
                <td style={CELL}>{row.campaign_name}</td>
                <td style={{ ...CELL, color: "#475569" }}>{fmtTs(row.last_call_at)}</td>
                <td style={CELL}>
                  {row.next_action ? (
                    <Badge val={row.next_action} color={actionColor(row.next_action)} />
                  ) : "—"}
                </td>
                <td style={CELL}>
                  {row.sales_outcome ? (
                    <Badge
                      val={OUTCOME_LABELS[row.sales_outcome] ?? row.sales_outcome}
                      color={OUTCOME_COLORS[row.sales_outcome] ?? "#64748b"}
                    />
                  ) : "—"}
                </td>
                <td style={CELL}>
                  {row.status ? (
                    <Badge val={row.status} color={STATUS_COLORS[row.status] ?? "#94a3b8"} />
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function CampaignOverviewClient() {
  const today = todayIso();
  const [fromDate, setFromDate] = useState(today);
  const [toDate, setToDate] = useState(addDays(today, 7));
  const [rows, setRows] = useState<CampaignOverviewRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [campaignFilter, setCampaignFilter] = useState<string>("All");

  const [drillDown, setDrillDown] = useState<{ contactId: string; phone: string } | null>(null);

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

  const campaigns = ["All", "New Lead", "Cold Lead", "Inbound"];

  const displayRows = campaignFilter === "All"
    ? rows
    : rows.filter((r) => r.campaign_name === campaignFilter);

  // ── Drill-down view ──────────────────────────────────────────────────────────
  if (drillDown) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div style={{
          display: "flex", alignItems: "center", gap: "0.75rem",
          background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "0.75rem 1rem",
        }}>
          <button
            onClick={() => setDrillDown(null)}
            style={{
              display: "flex", alignItems: "center", gap: "0.35rem",
              padding: "0.35rem 0.875rem",
              background: "#f1f5f9", border: "1px solid #e2e8f0",
              borderRadius: 6, fontSize: "0.82rem", fontWeight: 600,
              color: "#475569", cursor: "pointer",
            }}
          >
            ← Back to Campaign Overview
          </button>
          <span style={{ fontSize: "0.85rem", color: "#64748b" }}>
            Contact:{" "}
            <strong style={{ color: "#1e293b", fontFamily: "monospace" }}>
              {drillDown.phone}
            </strong>
          </span>
        </div>
        <ContactLookupClient contactId={drillDown.contactId} />
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      {/* ── Controls ── */}
      <div style={{
        display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap",
        background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8,
        padding: "0.875rem 1rem",
      }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>Next action from</label>
          <input
            type="date" value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }}
          />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>Next action to</label>
          <input
            type="date" value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }}
          />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>Campaign</label>
          <select
            value={campaignFilter}
            onChange={(e) => setCampaignFilter(e.target.value)}
            style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }}
          >
            {campaigns.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <button
          onClick={load} disabled={loading}
          style={{
            padding: "0.4rem 1rem", background: "#3b82f6", color: "#fff",
            border: "none", borderRadius: 6, fontSize: "0.85rem", fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
        <span style={{ marginLeft: "auto", fontSize: "0.8rem", color: "#64748b", alignSelf: "center" }}>
          {loading ? "" : `${total} contact${total !== 1 ? "s" : ""} · ${fromDate} → ${toDate}`}
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
      {!loading && (
        <OverviewTable
          rows={displayRows}
          onDrillDown={(contactId, phone) => setDrillDown({ contactId, phone })}
          emptyMessage="No contacts in this window."
        />
      )}
    </div>
  );
}
