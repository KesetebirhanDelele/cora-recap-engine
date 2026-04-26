"use client";

/**
 * Lead Lifecycle Monitor — /lead-lifecycle
 *
 * Shows the full campaign journey per contact:
 *   - Summary row: Active / In VM sequence / Campaign switched / Finalized / Avg days to close
 *   - Filter bar: status + campaign
 *   - Table: one row per lead with journey fields
 *   - Polling: every 60 minutes, with a manual Refresh Now button
 */

import { useEffect, useRef, useState, useCallback } from "react";
import Link from "next/link";
import { fetchLeadLifecycle } from "@/lib/api";
import type { LeadLifecycleResponse, LeadLifecycleRow, LeadLifecycleSummary } from "@/types";
import ContactLookupClient from "@/components/ContactLookupClient";

// ── Polling interval ──────────────────────────────────────────────────────────
const POLL_MS = 60 * 60 * 1000; // 1 hour

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago",
  });
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric", timeZone: "America/Chicago",
  });
}

function vmLabel(tier: string | null): string {
  if (!tier) return "—";
  const map: Record<string, string> = { "0": "Tier 0", "1": "Tier 1", "2": "Tier 2", "3": "Tier 3 (terminal)" };
  return map[tier] ?? tier;
}

function statusBadge(row: LeadLifecycleRow): { label: string; color: string } {
  if (row.do_not_call) return { label: "DNC", color: "#ef4444" };
  if (row.status === "closed" || row.status === "terminal") return { label: "Finalized", color: "#6b7280" };
  if (row.vm_tier !== null && row.vm_tier !== "3") return { label: "VM Sequence", color: "#f59e0b" };
  return { label: "Active", color: "#16a34a" };
}

function intentLabel(intent: string | null): string {
  if (!intent) return "—";
  return intent.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

// ── Styles ────────────────────────────────────────────────────────────────────
const CARD: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
};

const STAT_CARD: React.CSSProperties = {
  ...CARD,
  padding: "0.75rem 1rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.25rem",
  flex: 1,
  minWidth: 120,
};

const LABEL_STYLE: React.CSSProperties = {
  fontSize: "0.72rem",
  fontWeight: 700,
  color: "#94a3b8",
  textTransform: "uppercase",
  letterSpacing: "0.07em",
};

const VALUE_STYLE: React.CSSProperties = {
  fontSize: "1.5rem",
  fontWeight: 700,
  color: "#1e293b",
  lineHeight: 1.1,
};

// ── Summary strip ─────────────────────────────────────────────────────────────
function SummaryStrip({ summary }: { summary: LeadLifecycleSummary }) {
  const stats = [
    { label: "Active",           value: summary.active,            color: "#16a34a" },
    { label: "In VM Sequence",   value: summary.in_vm_sequence,    color: "#f59e0b" },
    { label: "Campaign Switched",value: summary.campaign_switched, color: "#8b5cf6" },
    { label: "Finalized",        value: summary.finalized,         color: "#6b7280" },
    { label: "Avg Days to Close",
      value: summary.avg_days_to_close != null ? `${summary.avg_days_to_close}d` : "—",
      color: "#0ea5e9" },
  ];
  return (
    <div style={{ display: "flex", gap: "0.625rem", flexWrap: "wrap" }}>
      {stats.map(s => (
        <div key={s.label} style={STAT_CARD}>
          <div style={LABEL_STYLE}>{s.label}</div>
          <div style={{ ...VALUE_STYLE, color: s.color }}>{s.value.toLocaleString?.() ?? s.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Table ─────────────────────────────────────────────────────────────────────
const TH: React.CSSProperties = {
  padding: "0.5rem 0.75rem",
  textAlign: "left",
  fontSize: "0.72rem",
  fontWeight: 700,
  color: "#94a3b8",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  borderBottom: "1px solid #e2e8f0",
  whiteSpace: "nowrap",
  background: "#f8fafc",
};

const TD: React.CSSProperties = {
  padding: "0.5rem 0.75rem",
  fontSize: "0.82rem",
  color: "#1e293b",
  borderBottom: "1px solid #f1f5f9",
  verticalAlign: "middle",
  whiteSpace: "nowrap",
};

function LeadTable({ rows, onDrillDown }: { rows: LeadLifecycleRow[]; onDrillDown: (contactId: string, phone: string) => void }) {
  if (rows.length === 0) {
    return (
      <div style={{ color: "#94a3b8", padding: "2rem", textAlign: "center", fontSize: "0.875rem" }}>
        No leads match the current filters
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
        <thead>
          <tr>
            {[
              "Lead", "Phone", "Status", "Initial Campaign", "Current Campaign",
              "VM Tier", "First Contact", "Last Contact", "Days Active",
              "Calls", "SMS", "Email", "Last Intent", "Next Action",
              "Finalization",
            ].map(h => <th key={h} style={TH}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const badge = statusBadge(row);
            const hasName = row.lead_name && row.lead_name !== "Unknown";
            const displayName = hasName ? row.lead_name : (row.phone ?? row.contact_id.slice(0, 12));
            return (
              <tr key={row.contact_id} style={{ background: row.do_not_call ? "#fff7f7" : undefined }}>
                <td style={TD}>
                  <div style={{ fontWeight: 600 }}>{displayName}</div>
                  <div style={{ fontSize: "0.72rem", color: "#94a3b8" }}>{row.contact_id.slice(0, 12)}…</div>
                </td>
                <td style={TD}>
                  {row.phone ? (
                    <span
                      onClick={() => onDrillDown(row.phone!, row.phone!)}
                      style={{ color: "#3b82f6", cursor: "pointer", textDecoration: "underline" }}
                    >
                      {row.phone}
                    </span>
                  ) : "—"}
                </td>
                <td style={TD}>
                  <span style={{
                    background: badge.color + "18",
                    color: badge.color,
                    border: `1px solid ${badge.color}40`,
                    borderRadius: 4,
                    padding: "2px 7px",
                    fontSize: "0.72rem",
                    fontWeight: 700,
                  }}>
                    {badge.label}
                  </span>
                </td>
                <td style={TD}>{row.initial_campaign ?? "—"}</td>
                <td style={{ ...TD, color: row.current_campaign !== row.initial_campaign ? "#8b5cf6" : undefined }}>
                  {row.current_campaign ?? "—"}
                  {row.campaign_switches > 0 && (
                    <span style={{ marginLeft: 4, fontSize: "0.68rem", color: "#8b5cf6" }}>
                      ({row.campaign_switches}×)
                    </span>
                  )}
                </td>
                <td style={TD}>{vmLabel(row.vm_tier)}</td>
                <td style={TD}>{fmtDate(row.first_contact_at)}</td>
                <td style={TD}>{fmtDate(row.last_contact_at)}</td>
                <td style={{ ...TD, textAlign: "center" }}>
                  {row.days_active != null ? `${row.days_active}d` : "—"}
                </td>
                <td style={{ ...TD, textAlign: "center" }}>{row.total_calls}</td>
                <td style={{ ...TD, textAlign: "center" }}>{row.total_sms}</td>
                <td style={{ ...TD, textAlign: "center" }}>{row.total_email}</td>
                <td style={TD}>{intentLabel(row.last_intent)}</td>
                <td style={TD}>
                  {row.next_job_type ? (
                    <div>
                      <div style={{ fontSize: "0.72rem", fontWeight: 600 }}>
                        {row.next_job_type.replace(/_/g, " ")}
                      </div>
                      <div style={{ fontSize: "0.68rem", color: "#94a3b8" }}>{fmt(row.next_run_at)}</div>
                    </div>
                  ) : "—"}
                </td>
                <td style={TD}>
                  {row.finalized_at ? (
                    <div>
                      <div style={{ fontSize: "0.72rem" }}>{fmtDate(row.finalized_at)}</div>
                      {row.finalization_reason && (
                        <div style={{ fontSize: "0.68rem", color: "#94a3b8" }}>{row.finalization_reason}</div>
                      )}
                    </div>
                  ) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function LeadLifecyclePage() {
  const [data, setData]         = useState<LeadLifecycleResponse | null>(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [statusFilter, setStatusFilter]   = useState<"all" | "active" | "finalized" | "vm" | "dnc">("all");
  const [campaignFilter, setCampaignFilter] = useState("all");
  const [offset, setOffset]     = useState(0);
  const [drillDown, setDrillDown] = useState<{ contactId: string; phone: string } | null>(null);
  const LIMIT = 100;
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (status: typeof statusFilter, campaign: string, off: number) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchLeadLifecycle({ status, campaign, limit: LIMIT, offset: off });
      setData(result);
      setLastUpdated(new Date());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + polling
  useEffect(() => {
    load(statusFilter, campaignFilter, offset);
    timerRef.current = setInterval(() => load(statusFilter, campaignFilter, offset), POLL_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [statusFilter, campaignFilter, offset, load]);

  const refresh = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    load(statusFilter, campaignFilter, offset);
    timerRef.current = setInterval(() => load(statusFilter, campaignFilter, offset), POLL_MS);
  };

  // ── Drill-down view ────────────────────────────────────────────────────────
  if (drillDown) {
    return (
      <div style={{
        minHeight: "100vh", display: "flex", flexDirection: "column",
        background: "#f1f5f9", fontFamily: "system-ui, -apple-system, sans-serif", color: "#1e293b",
        padding: "0.75rem", gap: "1rem",
      }}>
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
              borderRadius: 6, fontSize: "0.82rem", cursor: "pointer", fontWeight: 600,
            }}
          >
            ← Back to Lead Lifecycle
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

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      flexDirection: "column",
      background: "#f1f5f9",
      fontFamily: "system-ui, -apple-system, sans-serif",
      color: "#1e293b",
    }}>
      {/* Top bar */}
      <div style={{
        height: 40, flexShrink: 0, background: "#ffffff",
        borderBottom: "1px solid #e2e8f0",
        display: "flex", alignItems: "center", padding: "0 1rem", gap: "0.625rem",
      }}>
        <Link href="/" style={{ color: "#64748b", textDecoration: "none", fontSize: "0.9rem" }}>
          ← Dashboard
        </Link>
        <span style={{ color: "#e2e8f0" }}>|</span>
        <span style={{ fontSize: "1rem", fontWeight: 600 }}>Lead Lifecycle Monitor</span>
        <span style={{ marginLeft: "auto", fontSize: "0.78rem", color: "#94a3b8" }}>
          {loading ? "Refreshing…" : lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString("en-US", { timeZone: "America/Chicago" })}` : ""}
        </span>
        {error && (
          <span style={{ fontSize: "0.78rem", color: "#ef4444" }}>⚠ {error}</span>
        )}
        <button
          onClick={refresh}
          disabled={loading}
          style={{
            padding: "4px 12px",
            fontSize: "0.78rem",
            fontWeight: 600,
            background: loading ? "#f1f5f9" : "#1e293b",
            color: loading ? "#94a3b8" : "#ffffff",
            border: "none",
            borderRadius: 6,
            cursor: loading ? "not-allowed" : "pointer",
          }}
        >
          {loading ? "Loading…" : "Refresh Now"}
        </button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: "0.75rem", display: "flex", flexDirection: "column", gap: "0.625rem" }}>

        {/* Summary strip */}
        {data && <SummaryStrip summary={data.summary} />}

        {/* Filter bar + table */}
        <div style={{ ...CARD, flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Filter bar */}
          <div style={{
            padding: "0.625rem 0.875rem",
            borderBottom: "1px solid #e2e8f0",
            display: "flex",
            alignItems: "center",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}>
            <span style={{ fontSize: "0.85rem", fontWeight: 700, color: "#b45309" }}>
              Lead Journey
            </span>
            {data && (
              <span style={{ fontSize: "0.8rem", color: "#94a3b8" }}>
                {data.total.toLocaleString()} leads
              </span>
            )}
            <div style={{ marginLeft: "auto", display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <label style={{ fontSize: "0.78rem", color: "#64748b" }}>Status</label>
              <select
                value={statusFilter}
                onChange={e => { setStatusFilter(e.target.value as typeof statusFilter); setOffset(0); }}
                style={{ fontSize: "0.78rem", padding: "3px 6px", borderRadius: 4, border: "1px solid #e2e8f0" }}
              >
                <option value="all">All</option>
                <option value="active">Active</option>
                <option value="vm">In VM Sequence</option>
                <option value="finalized">Finalized</option>
                <option value="dnc">Do Not Call</option>
              </select>
              <label style={{ fontSize: "0.78rem", color: "#64748b" }}>Campaign</label>
              <select
                value={campaignFilter}
                onChange={e => { setCampaignFilter(e.target.value); setOffset(0); }}
                style={{ fontSize: "0.78rem", padding: "3px 6px", borderRadius: 4, border: "1px solid #e2e8f0" }}
              >
                <option value="all">All</option>
                <option value="Cold Lead">Cold Lead</option>
                <option value="New Lead">New Lead</option>
                <option value="Inbound">Inbound</option>
              </select>
            </div>
          </div>

          {/* Table */}
          <div style={{ flex: 1, overflowY: "auto" }}>
            <LeadTable rows={data?.rows ?? []} onDrillDown={(contactId, phone) => setDrillDown({ contactId, phone })} />
          </div>

          {/* Pagination */}
          {data && data.total > LIMIT && (
            <div style={{
              padding: "0.5rem 0.875rem",
              borderTop: "1px solid #e2e8f0",
              display: "flex",
              alignItems: "center",
              gap: "0.75rem",
              fontSize: "0.78rem",
              color: "#64748b",
            }}>
              <span>
                {offset + 1}–{Math.min(offset + LIMIT, data.total)} of {data.total.toLocaleString()}
              </span>
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                style={{ padding: "3px 10px", fontSize: "0.78rem", borderRadius: 4,
                         border: "1px solid #e2e8f0", cursor: offset === 0 ? "not-allowed" : "pointer",
                         background: offset === 0 ? "#f8fafc" : "#ffffff" }}
              >
                ← Prev
              </button>
              <button
                disabled={offset + LIMIT >= data.total}
                onClick={() => setOffset(offset + LIMIT)}
                style={{ padding: "3px 10px", fontSize: "0.78rem", borderRadius: 4,
                         border: "1px solid #e2e8f0",
                         cursor: offset + LIMIT >= data.total ? "not-allowed" : "pointer",
                         background: offset + LIMIT >= data.total ? "#f8fafc" : "#ffffff" }}
              >
                Next →
              </button>
            </div>
          )}
        </div>

        {/* Polling note */}
        <div style={{ fontSize: "0.72rem", color: "#cbd5e1", textAlign: "right" }}>
          Auto-refreshes every 60 minutes · next refresh resets on manual Refresh Now
        </div>
      </div>
    </div>
  );
}
