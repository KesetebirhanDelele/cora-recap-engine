"use client";

import { useState } from "react";
import type { StaffCallQualityRow } from "@/types";

const TYPE_LABEL: Record<string, string> = {
  sales: "Sales",
  support: "Support",
  other: "Other",
  unknown: "Unknown",
};

const SUMMARY_PREVIEW_LENGTH = 120;

function scoreColor(score: number | null): { bg: string; color: string } {
  if (score === null) return { bg: "#f1f5f9", color: "#64748b" };
  if (score >= 80) return { bg: "#dcfce7", color: "#16a34a" };
  if (score >= 50) return { bg: "#fef9c3", color: "#a16207" };
  return { bg: "#fee2e2", color: "#dc2626" };
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-US", {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function SummaryCell({ summary }: { summary: string | null }) {
  const [expanded, setExpanded] = useState(false);

  if (!summary) {
    return <span style={{ color: "#cbd5e1" }}>—</span>;
  }
  if (summary.length <= SUMMARY_PREVIEW_LENGTH) {
    return <>{summary}</>;
  }

  return (
    <span>
      {expanded ? summary : `${summary.slice(0, SUMMARY_PREVIEW_LENGTH)}…`}
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          marginLeft: "0.4rem",
          border: "none",
          background: "none",
          color: "#3b82f6",
          fontSize: "0.75rem",
          fontWeight: 700,
          cursor: "pointer",
          padding: 0,
          whiteSpace: "nowrap",
        }}
      >
        {expanded ? "Show less" : "Show more"}
      </button>
    </span>
  );
}

function CallRow({ row }: { row: StaffCallQualityRow }) {
  const sc = scoreColor(row.quality_score);
  return (
    <tr style={{ borderBottom: "1px solid #f1f5f9", verticalAlign: "top" }}>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.8rem", color: "#334155", whiteSpace: "nowrap" }}>
        {fmtTs(row.call_time)}
      </td>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.8rem", color: "#334155", whiteSpace: "nowrap" }}>
        {row.lead_name ?? "—"}
      </td>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.8rem", color: "#334155", whiteSpace: "nowrap" }}>
        {row.lead_phone ?? "—"}
      </td>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.8rem", color: "#334155" }}>
        {row.rep_user_id ?? "—"}
      </td>
      <td style={{ padding: "0.55rem 0.75rem" }}>
        <span
          style={{
            fontSize: "0.72rem", fontWeight: 700, padding: "0.15rem 0.5rem", borderRadius: 5,
            background: "#eef2ff", color: "#4338ca",
          }}
        >
          {TYPE_LABEL[row.conversation_type ?? "unknown"]}
        </span>
      </td>
      <td style={{ padding: "0.55rem 0.75rem" }}>
        {row.call_connected ? (
          <span
            style={{
              display: "inline-block", minWidth: 32, textAlign: "center",
              fontSize: "0.78rem", fontWeight: 700, padding: "0.15rem 0.5rem", borderRadius: 5,
              background: sc.bg, color: sc.color,
            }}
          >
            {row.quality_score ?? "—"}
          </span>
        ) : (
          <span style={{ fontSize: "0.75rem", color: "#94a3b8" }}>not connected</span>
        )}
      </td>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.78rem", color: "#475569", maxWidth: 420 }}>
        <SummaryCell summary={row.summary} />
      </td>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.78rem" }}>
        {row.flagged_reason ? (
          <span style={{ color: "#dc2626", fontWeight: 600 }}>⚑ {row.flagged_reason}</span>
        ) : (
          <span style={{ color: "#cbd5e1" }}>—</span>
        )}
      </td>
    </tr>
  );
}

export default function StaffCallQualityTable({ rows }: { rows: StaffCallQualityRow[] }) {
  return (
    <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              {["Call Time", "Lead Name", "Lead Phone", "Rep", "Type", "Score", "Summary", "Flag"].map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: "left", padding: "0.5rem 0.75rem", fontSize: "0.72rem", fontWeight: 700,
                    color: "#64748b", textTransform: "uppercase", letterSpacing: "0.04em", whiteSpace: "nowrap",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <CallRow key={row.ghl_message_id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && (
        <div style={{ padding: "1.5rem", textAlign: "center", color: "#94a3b8", fontSize: "0.85rem" }}>
          Nothing to show yet.
        </div>
      )}
    </div>
  );
}
