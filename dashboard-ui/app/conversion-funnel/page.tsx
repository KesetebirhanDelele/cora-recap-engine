"use client";

/**
 * Sales Queue View — /conversion-funnel (path kept for routing stability)
 *
 * Transforms the old "Recent Calls" list into an actionable sales queue with:
 *   • Priority ranking  (🔴 urgent / 🟡 review / ⚪ none)
 *   • Lead name + phone
 *   • Sales score (0-100) and recommended action
 *   • Inline recording playback and transcript expand
 *   • Post-call outcome form (Call Now / Mark Done)
 *   • CSV export of the current filtered + sorted dataset
 *
 * Sorting: sales_priority DESC → sales_score DESC → last_call_minutes_ago ASC
 * Row highlight: urgent + last_call > 15 min → red tint
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchRecentCalls, saveSalesOutcome } from "@/lib/api";
import type { RecentCallRow, SalesOutcomeRequest } from "@/types";

// ── Constants ─────────────────────────────────────────────────────────────────

const VOICE_AGENTS = [
  { label: "All",      value: "" },
  { label: "NewLead",  value: "NewLead" },
  { label: "ColdLead", value: "ColdLead" },
  { label: "Inbound",  value: "Inbound" },
];

const PRIORITY_ORDER = { urgent: 0, review: 1, none: 2 };

const PRIORITY_CONFIG = {
  urgent: { icon: "🔴", label: "Urgent", color: "#dc2626", bg: "#fff1f2" },
  review: { icon: "🟡", label: "Review", color: "#d97706", bg: "#fffbeb" },
  none:   { icon: "⚪", label: "—",      color: "#94a3b8", bg: "#ffffff" },
} as const;

const OUTCOMES = [
  { value: "booked",         label: "Booked" },
  { value: "follow_up",      label: "Follow Up" },
  { value: "not_interested", label: "Not Interested" },
  { value: "no_answer",      label: "No Answer" },
  { value: "voicemail",      label: "Voicemail" },
  { value: "wrong_number",   label: "Wrong Number" },
];

const NEXT_ACTIONS = ["Call", "SMS", "Email", "Meeting"];

const TERMINAL_OUTCOMES = new Set(["booked", "not_interested", "wrong_number"]);
const REQUIRES_FOLLOWUP = new Set(["follow_up"]);

// ── Utility ───────────────────────────────────────────────────────────────────

function defaultDates() {
  const to = new Date();
  const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago",
  }) + " CST";
}

function fmtDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

function fmtMinutesAgo(min: number | null): string {
  if (min === null) return "—";
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60), rem = min % 60;
  if (h < 24) return rem ? `${h}h ${rem}m ago` : `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function sortCalls(calls: RecentCallRow[]): RecentCallRow[] {
  return [...calls].sort((a, b) => {
    const pa = PRIORITY_ORDER[a.sales_priority] ?? 2;
    const pb = PRIORITY_ORDER[b.sales_priority] ?? 2;
    if (pa !== pb) return pa - pb;
    if (b.sales_score !== a.sales_score) return b.sales_score - a.sales_score;
    const ma = a.last_call_minutes_ago ?? 999999;
    const mb = b.last_call_minutes_ago ?? 999999;
    return ma - mb;
  });
}

function escapeCsvCell(val: string | number | null | undefined): string {
  if (val === null || val === undefined) return "";
  const s = String(val);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function downloadCSV(calls: RecentCallRow[]) {
  const headers = [
    "timestamp", "lead_name", "phone", "intent", "status",
    "duration_seconds", "voice_agent", "campaign",
    "sales_priority", "sales_score", "attempts",
    "last_call_minutes_ago", "transcript", "recording_url", "recommended_action",
  ];
  const rows = calls.map((c) => [
    escapeCsvCell(c.call_time),
    escapeCsvCell(c.lead_name),
    escapeCsvCell(c.phone),
    escapeCsvCell(c.detected_intent),
    escapeCsvCell(c.status),
    escapeCsvCell(c.duration_seconds),
    escapeCsvCell(c.voice_agent),
    escapeCsvCell(c.campaign_name),
    escapeCsvCell(c.sales_priority),
    escapeCsvCell(c.sales_score),
    escapeCsvCell(c.attempts),
    escapeCsvCell(c.last_call_minutes_ago),
    escapeCsvCell(c.transcript),
    escapeCsvCell(c.recording_url),
    escapeCsvCell(c.recommended_action),
  ].join(","));

  const csv = [headers.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sales-queue-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Shared styles ─────────────────────────────────────────────────────────────

const CARD: React.CSSProperties = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8 };

const HEAD: React.CSSProperties = {
  padding: "0.45rem 0.75rem",
  background: "#f8fafc",
  borderBottom: "2px solid #e2e8f0",
  fontSize: "0.7rem",
  fontWeight: 700,
  color: "#64748b",
  textTransform: "uppercase" as const,
  letterSpacing: "0.06em",
  whiteSpace: "nowrap",
};

const CELL: React.CSSProperties = {
  padding: "0.5rem 0.75rem",
  borderBottom: "1px solid #f1f5f9",
  fontSize: "0.8rem",
  color: "#334155",
  verticalAlign: "middle",
};

const BTN = (variant: "primary" | "ghost" | "danger" = "ghost"): React.CSSProperties => ({
  padding: "0.25rem 0.6rem",
  border: "1px solid",
  borderRadius: 5,
  fontSize: "0.72rem",
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
  ...(variant === "primary"
    ? { background: "#1e293b", color: "#fff", borderColor: "#1e293b" }
    : variant === "danger"
    ? { background: "#fff", color: "#dc2626", borderColor: "#fca5a5" }
    : { background: "#fff", color: "#475569", borderColor: "#e2e8f0" }),
});

// ── Outcome form (inline row) ─────────────────────────────────────────────────

interface OutcomeFormProps {
  contactId: string;
  colSpan: number;
  onSave: (contactId: string, outcome: string, isTerminal: boolean) => void;
  onCancel: () => void;
}

function OutcomeForm({ contactId, colSpan, onSave, onCancel }: OutcomeFormProps) {
  const [outcome, setOutcome] = useState("");
  const [nextAction, setNextAction] = useState("");
  const [followUpAt, setFollowUpAt] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    if (!outcome) { setError("Outcome is required."); return; }
    if (REQUIRES_FOLLOWUP.has(outcome) && !nextAction) { setError("Next action is required for follow-up."); return; }
    if (nextAction && !followUpAt) { setError("Follow-up time is required when next action is set."); return; }
    if (notes.length > 200) { setError("Notes must be 200 characters or fewer."); return; }

    const operatorId =
      typeof window !== "undefined"
        ? localStorage.getItem("operator_id") ?? "sales-agent"
        : "sales-agent";

    const body: SalesOutcomeRequest = {
      contact_id: contactId,
      sales_outcome: outcome,
      sales_next_action: nextAction || null,
      sales_follow_up_at: followUpAt ? `${followUpAt}:00Z` : null,
      sales_notes: notes || null,
      updated_by: operatorId,
    };

    setSaving(true);
    setError(null);
    try {
      const res = await saveSalesOutcome(body);
      onSave(contactId, res.sales_outcome, res.is_terminal);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  const FIELD_LABEL: React.CSSProperties = { fontSize: "0.7rem", fontWeight: 600, color: "#64748b", marginBottom: 3 };
  const INPUT: React.CSSProperties = {
    border: "1px solid #e2e8f0", borderRadius: 5, padding: "0.3rem 0.5rem",
    fontSize: "0.8rem", color: "#1e293b", background: "#fff", width: "100%",
  };

  return (
    <tr>
      <td
        colSpan={colSpan}
        style={{ padding: "0.75rem 1rem", background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}
      >
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end" }}>
          {/* Outcome */}
          <div style={{ display: "flex", flexDirection: "column", minWidth: 140 }}>
            <div style={FIELD_LABEL}>Outcome *</div>
            <select value={outcome} onChange={(e) => setOutcome(e.target.value)} style={INPUT}>
              <option value="">— select —</option>
              {OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          {/* Next Action */}
          <div style={{ display: "flex", flexDirection: "column", minWidth: 130 }}>
            <div style={FIELD_LABEL}>
              Next Action {REQUIRES_FOLLOWUP.has(outcome) ? "*" : ""}
            </div>
            <select value={nextAction} onChange={(e) => setNextAction(e.target.value)} style={INPUT}>
              <option value="">— none —</option>
              {NEXT_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>

          {/* Follow-up time */}
          <div style={{ display: "flex", flexDirection: "column", minWidth: 170 }}>
            <div style={FIELD_LABEL}>Follow-up {nextAction ? "*" : ""}</div>
            <input
              type="datetime-local"
              value={followUpAt}
              onChange={(e) => setFollowUpAt(e.target.value)}
              style={INPUT}
            />
          </div>

          {/* Notes */}
          <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 160 }}>
            <div style={FIELD_LABEL}>Notes ({notes.length}/200)</div>
            <input
              type="text"
              maxLength={200}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional…"
              style={INPUT}
            />
          </div>

          {/* Buttons */}
          <div style={{ display: "flex", gap: "0.4rem", alignSelf: "flex-end" }}>
            <button
              onClick={handleSave}
              disabled={saving}
              style={{ ...BTN("primary"), opacity: saving ? 0.6 : 1 }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={onCancel} style={BTN("ghost")}>Cancel</button>
          </div>
        </div>
        {error && (
          <div style={{ marginTop: "0.4rem", fontSize: "0.75rem", color: "#dc2626" }}>{error}</div>
        )}
      </td>
    </tr>
  );
}

// ── Single call row ───────────────────────────────────────────────────────────

interface CallRowProps {
  call: RecentCallRow;
  index: number;
  activeForm: "outcome" | "transcript" | null;
  onToggleForm: (type: "outcome" | "transcript") => void;
  onOutcomeSaved: (contactId: string, outcome: string, isTerminal: boolean) => void;
}

function SalesCallRow({ call, index, activeForm, onToggleForm, onOutcomeSaved }: CallRowProps) {
  const pc = PRIORITY_CONFIG[call.sales_priority];
  const isHighlighted = call.sales_priority === "urgent" && (call.last_call_minutes_ago ?? 0) > 15;
  const rowBg = isHighlighted ? "#fff1f2" : index % 2 === 0 ? "#fff" : "#fafafa";

  const COL_COUNT = 9; // number of <th> columns

  return (
    <>
      <tr style={{ background: rowBg }}>
        {/* Priority */}
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>
          <span
            title={pc.label}
            style={{
              display: "inline-flex", alignItems: "center", gap: "0.3rem",
              padding: "0.15rem 0.45rem", borderRadius: 4,
              background: pc.bg, color: pc.color,
              fontSize: "0.72rem", fontWeight: 700, border: `1px solid ${pc.color}20`,
            }}
          >
            {pc.icon} {pc.label}
          </span>
        </td>

        {/* Lead Name → contact drill-down */}
        <td style={{ ...CELL, maxWidth: 160 }}>
          <Link
            href={`/lead/${encodeURIComponent(call.contact_id)}`}
            style={{ color: "#2563eb", textDecoration: "none", fontWeight: 600, fontSize: "0.8rem" }}
          >
            {call.lead_name}
          </Link>
          {call.attempts > 1 && (
            <div style={{ fontSize: "0.68rem", color: "#94a3b8", marginTop: 1 }}>
              {call.attempts} attempts
            </div>
          )}
        </td>

        {/* Phone */}
        <td style={{ ...CELL }}>
          <span style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>{call.phone}</span>
        </td>

        {/* Intent */}
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>
          {call.detected_intent ? (
            <span style={{ fontSize: "0.75rem", color: "#475569" }}>
              {call.detected_intent.replace(/_/g, " ")}
            </span>
          ) : "—"}
        </td>

        {/* Last Call */}
        <td style={{ ...CELL, whiteSpace: "nowrap", color: "#64748b", fontSize: "0.75rem" }}>
          {fmtMinutesAgo(call.last_call_minutes_ago)}
          <div style={{ fontSize: "0.68rem", color: "#94a3b8" }}>{fmtTs(call.call_time)}</div>
        </td>

        {/* Score */}
        <td style={{ ...CELL, textAlign: "center" }}>
          <span style={{
            display: "inline-block", padding: "0.15rem 0.45rem", borderRadius: 4,
            background: call.sales_score >= 80 ? "#dcfce7" : call.sales_score >= 40 ? "#fef9c3" : "#f1f5f9",
            color: call.sales_score >= 80 ? "#16a34a" : call.sales_score >= 40 ? "#a16207" : "#64748b",
            fontSize: "0.75rem", fontWeight: 700,
          }}>
            {call.sales_score}
          </span>
        </td>

        {/* Recording */}
        <td style={{ ...CELL }}>
          {call.recording_url ? (
            <a
              href={call.recording_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#7c3aed", textDecoration: "none", fontWeight: 600, fontSize: "0.75rem" }}
            >
              ▶ Play
            </a>
          ) : (
            <span style={{ color: "#cbd5e1", fontSize: "0.75rem" }}>—</span>
          )}
        </td>

        {/* Transcript */}
        <td style={{ ...CELL, maxWidth: 200 }}>
          {call.transcript ? (
            <>
              <span style={{ fontSize: "0.72rem", color: "#64748b", display: "block", marginBottom: 3 }}>
                {call.transcript_preview ?? call.transcript.slice(0, 120)}
                {(call.transcript.length > 120) ? "…" : ""}
              </span>
              <button
                onClick={() => onToggleForm("transcript")}
                style={{ background: "none", border: "none", padding: 0, color: "#2563eb", fontSize: "0.72rem", cursor: "pointer", textDecoration: "underline dotted" }}
              >
                {activeForm === "transcript" ? "Hide" : "View full"}
              </button>
            </>
          ) : "—"}
        </td>

        {/* Action */}
        <td style={{ ...CELL, whiteSpace: "nowrap" }}>
          <div style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
            <button
              onClick={() => onToggleForm("outcome")}
              style={BTN("primary")}
            >
              {activeForm === "outcome" ? "Cancel" : "Call Now"}
            </button>
            <button
              onClick={() => onToggleForm("outcome")}
              style={BTN("ghost")}
            >
              Mark Done
            </button>
          </div>
          <div style={{ fontSize: "0.68rem", color: "#94a3b8", marginTop: 3 }}>
            {call.recommended_action}
          </div>
        </td>
      </tr>

      {/* Transcript expand */}
      {activeForm === "transcript" && call.transcript && (
        <tr style={{ background: "#f0f9ff" }}>
          <td colSpan={COL_COUNT} style={{ padding: "0.5rem 1rem 0.75rem", borderBottom: "1px solid #bae6fd" }}>
            <div style={{
              background: "#fff", border: "1px solid #bae6fd", borderRadius: 6,
              padding: "0.625rem 0.75rem", fontSize: "0.78rem", color: "#334155",
              lineHeight: 1.6, whiteSpace: "pre-wrap", maxHeight: 240, overflowY: "auto",
            }}>
              {call.transcript}
            </div>
          </td>
        </tr>
      )}

      {/* Outcome form */}
      {activeForm === "outcome" && (
        <OutcomeForm
          contactId={call.contact_id}
          colSpan={COL_COUNT}
          onSave={onOutcomeSaved}
          onCancel={() => onToggleForm("outcome")}
        />
      )}
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SalesQueuePage() {
  const defaults = defaultDates();
  const [fromDate, setFromDate] = useState(defaults.from);
  const [toDate, setToDate] = useState(defaults.to);
  const [agentIdx, setAgentIdx] = useState(0);
  const [allCalls, setAllCalls] = useState<RecentCallRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Track which row has an open form (outcome | transcript) by contact_id+type
  const [openForm, setOpenForm] = useState<{ contactId: string; type: "outcome" | "transcript" } | null>(null);

  // Contacts removed from active queue (terminal outcome saved)
  const [completedIds, setCompletedIds] = useState<Set<string>>(new Set());

  const activeVoiceAgent = VOICE_AGENTS[agentIdx]?.value || undefined;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setOpenForm(null);
    try {
      const data = await fetchRecentCalls({
        from_date: fromDate ? `${fromDate}T00:00:00Z` : undefined,
        to_date: toDate ? `${toDate}T23:59:59Z` : undefined,
        voice_agent: activeVoiceAgent,
      });
      setAllCalls(sortCalls(data.calls));
      setTotal(data.total);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, activeVoiceAgent]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load]);

  function toggleForm(contactId: string, type: "outcome" | "transcript") {
    setOpenForm((prev) =>
      prev?.contactId === contactId && prev?.type === type ? null : { contactId, type }
    );
  }

  function handleOutcomeSaved(contactId: string, _outcome: string, isTerminal: boolean) {
    setOpenForm(null);
    if (isTerminal) {
      setCompletedIds((prev) => new Set([...prev, contactId]));
    }
  }

  const activeCalls = allCalls.filter((c) => !completedIds.has(c.contact_id));
  const completedCalls = allCalls.filter((c) => completedIds.has(c.contact_id));
  const urgentCount = activeCalls.filter((c) => c.sales_priority === "urgent").length;

  return (
    <div style={{ minHeight: "100vh", background: "#f1f5f9", fontFamily: "system-ui, -apple-system, sans-serif", color: "#1e293b" }}>

      {/* ── Topbar ──────────────────────────────────────────────────────────── */}
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
        <span style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b" }}>Sales Queue</span>
        {urgentCount > 0 && (
          <span style={{
            background: "#dc2626", color: "#fff", borderRadius: 10,
            padding: "0.1rem 0.5rem", fontSize: "0.72rem", fontWeight: 700,
          }}>
            {urgentCount} urgent
          </span>
        )}
        {loading && <span style={{ fontSize: "0.75rem", color: "#94a3b8", marginLeft: 4 }}>Loading…</span>}
        {error && <span style={{ fontSize: "0.75rem", color: "#ef4444", marginLeft: 4 }}>⚠ {error}</span>}
        <div style={{ marginLeft: "auto", display: "flex", gap: "0.5rem" }}>
          <button
            onClick={() => downloadCSV(activeCalls)}
            disabled={activeCalls.length === 0}
            style={{
              padding: "0.3rem 0.85rem", border: "1px solid #e2e8f0", borderRadius: 6,
              fontSize: "0.8rem", fontWeight: 600, cursor: "pointer",
              background: "#fff", color: "#475569",
              opacity: activeCalls.length === 0 ? 0.4 : 1,
            }}
          >
            ↓ CSV
          </button>
        </div>
      </div>

      {/* ── Content ─────────────────────────────────────────────────────────── */}
      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "1.5rem 1.5rem 3rem" }}>

        {/* Page heading */}
        <div style={{ marginBottom: "1.25rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.35rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.025em" }}>
            Sales Queue
          </h1>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
            Calls ≥ 30s with transcript and recording · sorted by priority → score → recency.
          </p>
        </div>

        {/* ── Filters ───────────────────────────────────────────────────────── */}
        <div style={{ ...CARD, padding: "0.875rem 1rem", marginBottom: "1rem", display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748b" }}>From</label>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)}
              style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748b" }}>To</label>
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)}
              style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.35rem 0.6rem", fontSize: "0.85rem", color: "#1e293b", background: "#fff" }} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.72rem", fontWeight: 600, color: "#64748b" }}>Voice Agent</label>
            <div style={{ display: "flex", gap: "0.25rem" }}>
              {VOICE_AGENTS.map((a, idx) => (
                <button key={a.label} onClick={() => setAgentIdx(idx)} style={{
                  padding: "0.35rem 0.75rem", border: "1px solid #e2e8f0",
                  borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: "pointer",
                  background: agentIdx === idx ? "#1e293b" : "#fff",
                  color: agentIdx === idx ? "#fff" : "#64748b",
                }}>
                  {a.label}
                </button>
              ))}
            </div>
          </div>
          <button onClick={load} disabled={loading} style={{
            padding: "0.4rem 1rem", background: "#3b82f6", color: "#fff",
            border: "none", borderRadius: 6, fontSize: "0.85rem", fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, alignSelf: "flex-end",
          }}>
            {loading ? "Loading…" : "Refresh"}
          </button>
          <span style={{ marginLeft: "auto", fontSize: "0.8rem", color: "#64748b", alignSelf: "center" }}>
            {!loading && `${total} call${total !== 1 ? "s" : ""}`}
          </span>
        </div>

        {/* ── Error / Empty ─────────────────────────────────────────────────── */}
        {error ? (
          <div style={{ ...CARD, padding: "1rem", color: "#991b1b", background: "#fef2f2", border: "1px solid #fecaca" }}>{error}</div>
        ) : !loading && allCalls.length === 0 ? (
          <div style={{ ...CARD, padding: "3rem", textAlign: "center", color: "#64748b", fontSize: "0.9rem" }}>
            No calls matching the current filters. Try expanding the date range.
          </div>
        ) : (
          <>
            {/* ── Active queue ─────────────────────────────────────────────── */}
            <div style={{ marginBottom: "0.5rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "0.85rem", fontWeight: 700, color: "#1e293b" }}>
                Active Queue
              </span>
              <span style={{ background: "#e2e8f0", borderRadius: 10, padding: "0.1rem 0.5rem", fontSize: "0.72rem", color: "#475569", fontWeight: 700 }}>
                {activeCalls.length}
              </span>
            </div>
            <div style={{ ...CARD, overflow: "hidden", marginBottom: "1.5rem" }}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["Priority", "Name", "Phone", "Intent", "Last Call", "Score", "Recording", "Transcript", "Action"].map((h) => (
                        <th key={h} style={HEAD}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {activeCalls.length === 0 ? (
                      <tr>
                        <td colSpan={9} style={{ ...CELL, textAlign: "center", color: "#94a3b8", padding: "2rem" }}>
                          No active leads.
                        </td>
                      </tr>
                    ) : (
                      activeCalls.map((call, i) => (
                        <SalesCallRow
                          key={`${call.contact_id}-${i}`}
                          call={call}
                          index={i}
                          activeForm={
                            openForm?.contactId === call.contact_id ? openForm.type : null
                          }
                          onToggleForm={(type) => toggleForm(call.contact_id, type)}
                          onOutcomeSaved={handleOutcomeSaved}
                        />
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* ── Completed (this session) ──────────────────────────────────── */}
            {completedCalls.length > 0 && (
              <>
                <div style={{ marginBottom: "0.5rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span style={{ fontSize: "0.85rem", fontWeight: 700, color: "#64748b" }}>
                    Completed this session
                  </span>
                  <span style={{ background: "#dcfce7", borderRadius: 10, padding: "0.1rem 0.5rem", fontSize: "0.72rem", color: "#16a34a", fontWeight: 700 }}>
                    {completedCalls.length}
                  </span>
                </div>
                <div style={{ ...CARD, overflow: "hidden", opacity: 0.75 }}>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                      <thead>
                        <tr>
                          {["Priority", "Name", "Phone", "Intent", "Last Call", "Score", "Recording", "Transcript", "Action"].map((h) => (
                            <th key={h} style={{ ...HEAD, background: "#f0fdf4" }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {completedCalls.map((call, i) => (
                          <SalesCallRow
                            key={`done-${call.contact_id}-${i}`}
                            call={call}
                            index={i}
                            activeForm={
                              openForm?.contactId === call.contact_id ? openForm.type : null
                            }
                            onToggleForm={(type) => toggleForm(call.contact_id, type)}
                            onOutcomeSaved={handleOutcomeSaved}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
