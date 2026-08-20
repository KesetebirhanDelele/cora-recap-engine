"use client";

import { useState } from "react";
import type { WebhookFailuresResponse } from "@/types";
import { advanceStaleLeadAction, recoverCallWebhook, ignoreWebhookFailure } from "@/lib/api";

function pctColor(pct: number | null): string {
  if (pct === null) return "#94a3b8";
  if (pct >= 95)    return "#10b981";
  if (pct >= 80)    return "#f59e0b";
  return "#ef4444";
}

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } catch {
    return iso;
  }
}

function RowActions({ jobId, contactId, onDone }: { jobId: string; contactId: string; onDone: () => void }) {
  const [loading, setLoading]       = useState<"voicemail" | "no_answer" | "recover" | "ignore" | null>(null);
  const [result, setResult]         = useState<string | null>(null);
  const [showCallInput, setShowCallInput] = useState(false);
  const [callId, setCallId]         = useState("");

  async function act(outcome: "voicemail" | "no_answer") {
    setLoading(outcome);
    setResult(null);
    try {
      const r = await advanceStaleLeadAction(contactId, outcome);
      const label =
        r.action === "finalized"        ? "Finalized" :
        r.action === "closed"           ? "Closed" :
        r.action === "advanced"         ? `→ Tier ${r.tier_to}` :
        r.action === "retry_scheduled"  ? "Retry scheduled" :
        r.action;
      setResult(label);
      setTimeout(onDone, 1500);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setResult(`Error: ${msg}`);
    } finally {
      setLoading(null);
    }
  }

  async function recover() {
    const trimmed = callId.trim();
    if (!trimmed) return;
    setLoading("recover");
    setResult(null);
    try {
      await recoverCallWebhook(contactId, trimmed);
      setResult("Processing…");
      setTimeout(onDone, 1500);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setResult(`Error: ${msg}`);
    } finally {
      setLoading(null);
    }
  }

  async function ignore() {
    setLoading("ignore");
    setResult(null);
    try {
      await ignoreWebhookFailure(jobId, contactId);
      setResult("Ignored");
      setTimeout(onDone, 1200);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setResult(`Error: ${msg}`);
    } finally {
      setLoading(null);
    }
  }

  if (result) {
    return (
      <span style={{
        fontSize: "0.65rem", fontWeight: 600,
        color: result.startsWith("Error") ? "#dc2626" : "#16a34a",
      }}>
        {result}
      </span>
    );
  }

  const busy = loading !== null;

  return (
    <div style={{ display: "flex", gap: "0.3rem", alignItems: "center", flexWrap: "wrap" }}>
      <span style={{
        fontSize: "0.65rem", background: "#fee2e2", color: "#dc2626",
        borderRadius: 4, padding: "1px 6px", fontWeight: 600,
      }}>
        no webhook
      </span>
      <button
        disabled={busy}
        onClick={() => act("voicemail")}
        title="VM was left — advance tier"
        style={{
          fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4,
          border: "1px solid #16a34a",
          background: loading === "voicemail" ? "#f0fdf4" : "#fff",
          color: "#16a34a", cursor: busy ? "not-allowed" : "pointer", fontWeight: 600,
        }}
      >
        {loading === "voicemail" ? "…" : "VM Left"}
      </button>
      <button
        disabled={busy}
        onClick={() => act("no_answer")}
        title="No answer — retry or close"
        style={{
          fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4,
          border: "1px solid #f97316",
          background: loading === "no_answer" ? "#fff7ed" : "#fff",
          color: "#f97316", cursor: busy ? "not-allowed" : "pointer", fontWeight: 600,
        }}
      >
        {loading === "no_answer" ? "…" : "No Answer"}
      </button>
      {!showCallInput ? (
        <button
          disabled={busy}
          onClick={() => setShowCallInput(true)}
          title="Call completed — fetch from Synthflow and process"
          style={{
            fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4,
            border: "1px solid #6366f1",
            background: "#fff",
            color: "#6366f1", cursor: busy ? "not-allowed" : "pointer", fontWeight: 600,
          }}
        >
          Call Completed
        </button>
      ) : (
        <span style={{ display: "flex", gap: "0.25rem", alignItems: "center" }}>
          <input
            autoFocus
            value={callId}
            onChange={e => setCallId(e.target.value)}
            onKeyDown={e => e.key === "Enter" && recover()}
            placeholder="Synthflow Call ID"
            disabled={loading === "recover"}
            style={{
              fontSize: "0.65rem", padding: "2px 6px", borderRadius: 4,
              border: "1px solid #6366f1", width: 160, outline: "none",
              color: "#0f172a",
            }}
          />
          <button
            disabled={!callId.trim() || loading === "recover"}
            onClick={recover}
            style={{
              fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4,
              border: "1px solid #6366f1",
              background: loading === "recover" ? "#eef2ff" : "#6366f1",
              color: loading === "recover" ? "#6366f1" : "#fff",
              cursor: !callId.trim() || loading === "recover" ? "not-allowed" : "pointer",
              fontWeight: 600,
            }}
          >
            {loading === "recover" ? "…" : "Fetch →"}
          </button>
          <button
            disabled={loading === "recover"}
            onClick={() => { setShowCallInput(false); setCallId(""); }}
            style={{
              fontSize: "0.65rem", padding: "2px 5px", borderRadius: 4,
              border: "1px solid #cbd5e1", background: "#fff",
              color: "#64748b", cursor: "pointer",
            }}
          >
            ✕
          </button>
        </span>
      )}
      <button
        disabled={busy}
        onClick={ignore}
        title="Dismiss this row — call was already handled or is not actionable"
        style={{
          fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4,
          border: "1px solid #94a3b8",
          background: loading === "ignore" ? "#f8fafc" : "#fff",
          color: "#64748b", cursor: busy ? "not-allowed" : "pointer", fontWeight: 600,
        }}
      >
        {loading === "ignore" ? "…" : "Ignore"}
      </button>
    </div>
  );
}

interface Props {
  data: WebhookFailuresResponse;
  onRefresh?: () => void;
}

export default function WebhookFailurePanel({ data, onRefresh }: Props) {
  const [open, setOpen] = useState(false);
  const { summary, failures } = data;
  const { total_launched, got_webhook, missing, webhook_pct } = summary;
  const color = pctColor(webhook_pct);
  const hasFailures = missing > 0;

  return (
    <div style={{
      background:   "#ffffff",
      border:       `1px solid ${hasFailures ? "#fca5a5" : "#e2e8f0"}`,
      borderRadius: 8,
      overflow:     "hidden",
      boxShadow:    "0 1px 3px rgba(0,0,0,0.05)",
    }}>

      {/* ── Header ─────────────────────────────────────────────────────────────── */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(o => !o)}
        onKeyDown={e => (e.key === "Enter" || e.key === " ") && setOpen(o => !o)}
        style={{
          display: "flex", alignItems: "center", gap: "1rem",
          padding: "0.875rem 1.25rem", cursor: "pointer", userSelect: "none",
          background: hasFailures ? "#fff7f7" : "#ffffff",
        }}
      >
        <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0f172a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Webhook Delivery — 24h
        </span>
        <span style={{
          fontSize: "0.72rem", fontWeight: 700, color, background: `${color}18`,
          border: `1px solid ${color}40`, borderRadius: 4, padding: "2px 8px",
        }}>
          {webhook_pct !== null ? `${webhook_pct}%` : "—"} delivery
        </span>
        <span style={{ fontSize: "0.72rem", color: "#64748b" }}>
          {got_webhook}/{total_launched} received
        </span>
        {hasFailures && (
          <span style={{
            fontSize: "0.72rem", fontWeight: 600, color: "#dc2626",
            background: "#fee2e2", borderRadius: 4, padding: "2px 8px",
          }}>
            {missing} missing
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: "0.7rem", color: "#94a3b8" }}>
          {open ? "▲ hide" : "▼ show jobs"}
        </span>
      </div>

      {/* ── Drawer ─────────────────────────────────────────────────────────────── */}
      {open && (
        <div style={{ borderTop: "1px solid #f1f5f9", overflowX: "auto" }}>
          {failures.length === 0 ? (
            <p style={{ padding: "1.25rem 1.5rem", fontSize: "0.8rem", color: "#94a3b8", margin: 0 }}>
              No webhook failures in the last 24 hours.
            </p>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.72rem" }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                  {["Contact", "Campaign", "Job Placed At", "Executed At", "Age (min)", "Action"].map(h => (
                    <th key={h} style={{ padding: "0.5rem 0.875rem", textAlign: "left", fontWeight: 600, color: "#475569", whiteSpace: "nowrap" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {failures.map((row, i) => (
                  <tr
                    key={row.job_id}
                    style={{ borderBottom: "1px solid #f1f5f9", background: i % 2 === 0 ? "#ffffff" : "#fafafa" }}
                  >
                    <td style={{ padding: "0.45rem 0.875rem", fontFamily: "monospace", color: "#334155" }}>
                      {row.contact_id ? (
                        <a
                          href={`/lead-lifecycle?contact_id=${row.contact_id}`}
                          style={{ color: "#6366f1", textDecoration: "none" }}
                          title="View lead lifecycle"
                        >
                          {row.contact_id.slice(0, 12)}…
                        </a>
                      ) : "—"}
                    </td>
                    <td style={{ padding: "0.45rem 0.875rem", color: "#475569" }}>
                      {row.campaign ?? "—"}
                    </td>
                    <td style={{ padding: "0.45rem 0.875rem", color: "#475569", whiteSpace: "nowrap" }}>
                      {fmtDate(row.placed_at)}
                    </td>
                    <td style={{ padding: "0.45rem 0.875rem", color: "#475569", whiteSpace: "nowrap" }}>
                      {fmtTs(row.executed_at)}
                    </td>
                    <td style={{
                      padding: "0.45rem 0.875rem",
                      color: row.minutes_since_execution !== null && row.minutes_since_execution > 60 ? "#dc2626" : "#475569",
                      fontWeight: row.minutes_since_execution !== null && row.minutes_since_execution > 60 ? 600 : 400,
                    }}>
                      {row.minutes_since_execution ?? "—"}
                    </td>
                    <td style={{ padding: "0.45rem 0.875rem" }}>
                      {row.contact_id ? (
                        <RowActions jobId={row.job_id} contactId={row.contact_id} onDone={onRefresh ?? (() => {})} />
                      ) : (
                        <span style={{ fontSize: "0.65rem", background: "#fee2e2", color: "#dc2626", borderRadius: 4, padding: "1px 6px", fontWeight: 600 }}>
                          no webhook
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p style={{ padding: "0.4rem 0.875rem", fontSize: "0.65rem", color: "#94a3b8", margin: 0, borderTop: "1px solid #f1f5f9" }}>
            Showing up to 200 failures · excludes jobs executed &lt;20 min ago · window: last 24 hours
          </p>
        </div>
      )}
    </div>
  );
}
