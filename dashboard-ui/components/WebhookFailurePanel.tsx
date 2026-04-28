"use client";

import { useState } from "react";
import type { WebhookFailuresResponse } from "@/types";

function pctColor(pct: number | null): string {
  if (pct === null) return "#94a3b8";
  if (pct >= 95)    return "#10b981"; // green
  if (pct >= 80)    return "#f59e0b"; // amber
  return "#ef4444";                   // red
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

interface Props {
  data: WebhookFailuresResponse;
}

export default function WebhookFailurePanel({ data }: Props) {
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

      {/* ── Header (always visible) ─────────────────────────────────────────── */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(o => !o)}
        onKeyDown={e => (e.key === "Enter" || e.key === " ") && setOpen(o => !o)}
        style={{
          display:       "flex",
          alignItems:    "center",
          gap:           "1rem",
          padding:       "0.875rem 1.25rem",
          cursor:        "pointer",
          userSelect:    "none",
          background:    hasFailures ? "#fff7f7" : "#ffffff",
        }}
      >
        {/* Title */}
        <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0f172a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Webhook Delivery — 24h
        </span>

        {/* Delivery rate badge */}
        <span style={{
          fontSize:     "0.72rem",
          fontWeight:   700,
          color:        color,
          background:   `${color}18`,
          border:       `1px solid ${color}40`,
          borderRadius: 4,
          padding:      "2px 8px",
        }}>
          {webhook_pct !== null ? `${webhook_pct}%` : "—"} delivery
        </span>

        {/* Stats */}
        <span style={{ fontSize: "0.72rem", color: "#64748b" }}>
          {got_webhook}/{total_launched} received
        </span>

        {hasFailures && (
          <span style={{
            fontSize:     "0.72rem",
            fontWeight:   600,
            color:        "#dc2626",
            background:   "#fee2e2",
            borderRadius: 4,
            padding:      "2px 8px",
          }}>
            {missing} missing
          </span>
        )}

        {/* Chevron */}
        <span style={{ marginLeft: "auto", fontSize: "0.7rem", color: "#94a3b8" }}>
          {open ? "▲ hide" : "▼ show jobs"}
        </span>
      </div>

      {/* ── Drawer (table) ──────────────────────────────────────────────────── */}
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
                  {["Contact", "Campaign", "Job Placed At", "Executed At", "Age (min)", ""].map(h => (
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
                    <td style={{ padding: "0.45rem 0.875rem", color: row.minutes_since_execution !== null && row.minutes_since_execution > 60 ? "#dc2626" : "#475569", fontWeight: row.minutes_since_execution !== null && row.minutes_since_execution > 60 ? 600 : 400 }}>
                      {row.minutes_since_execution ?? "—"}
                    </td>
                    <td style={{ padding: "0.45rem 0.875rem" }}>
                      <span style={{
                        fontSize:     "0.65rem",
                        background:   "#fee2e2",
                        color:        "#dc2626",
                        borderRadius: 4,
                        padding:      "1px 6px",
                        fontWeight:   600,
                      }}>
                        no webhook
                      </span>
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
