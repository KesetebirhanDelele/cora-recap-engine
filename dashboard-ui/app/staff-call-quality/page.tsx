import PageShell from "@/components/PageShell";
import { fetchStaffCallQuality } from "@/lib/api";
import type { StaffCallQualityResponse, StaffCallQualityRow } from "@/types";

export const revalidate = 0;

const TYPE_LABEL: Record<string, string> = {
  sales: "Sales",
  support: "Support",
  other: "Other",
  unknown: "Unknown",
};

const STAT_LABEL: React.CSSProperties = { fontSize: "0.75rem", fontWeight: 600, color: "#64748b" };
const STAT_VALUE: React.CSSProperties = { fontSize: "1.6rem", fontWeight: 800, color: "#0f172a", marginTop: 2 };

function StatTile({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderLeft: `3px solid ${accent ?? "#3b82f6"}`,
        borderRadius: 8,
        padding: "0.75rem 1rem",
        boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
      }}
    >
      <div style={STAT_LABEL}>{label}</div>
      <div style={STAT_VALUE}>{value}</div>
    </div>
  );
}

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

function CallRow({ row }: { row: StaffCallQualityRow }) {
  const sc = scoreColor(row.quality_score);
  return (
    <tr style={{ borderBottom: "1px solid #f1f5f9" }}>
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.8rem", color: "#334155", whiteSpace: "nowrap" }}>
        {fmtTs(row.call_time)}
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
      <td style={{ padding: "0.55rem 0.75rem", fontSize: "0.78rem", color: "#475569", maxWidth: 360 }}>
        {row.summary ?? "—"}
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

export default async function StaffCallQualityPage() {
  let data: StaffCallQualityResponse | null = null;
  let error: string | null = null;
  try {
    data = await fetchStaffCallQuality({ limit: 100 });
  } catch (e) {
    error = String(e);
  }

  return (
    <PageShell
      title="Staff Call Quality"
      subtitle="Sales-rep and support-staff calls pulled from GHL's native dialer, transcribed and scored (spec/23)."
    >
      {error && (
        <div style={{ marginBottom: "1rem", padding: "0.75rem 1rem", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, color: "#dc2626", fontSize: "0.85rem" }}>
          Failed to load: {error}
        </div>
      )}

      {data && data.total_scanned === 0 && (
        <div style={{ marginBottom: "1.25rem", padding: "0.75rem 1rem", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, color: "#92400e", fontSize: "0.85rem" }}>
          No calls analyzed yet. This scan is off by default (<code>STAFF_CALL_QUALITY_SCAN_ENABLED=false</code>) —
          nothing will appear here until it's turned on and has had time to run.
        </div>
      )}

      {data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "0.75rem", marginBottom: "1.25rem" }}>
            <StatTile label="Calls Scanned" value={data.total_scanned} accent="#3b82f6" />
            <StatTile label="Connected" value={data.total_connected} accent="#3b82f6" />
            <StatTile label="Analyzed" value={data.total_analyzed} accent="#3b82f6" />
            <StatTile label="Flagged" value={data.flagged_count} accent={data.flagged_count > 0 ? "#dc2626" : "#94a3b8"} />
            <StatTile
              label="Avg Score (Sales / Support)"
              value={`${data.avg_score_sales ?? "—"} / ${data.avg_score_support ?? "—"}`}
              accent="#8b5cf6"
            />
          </div>

          <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                  {["Call Time", "Rep", "Type", "Score", "Summary", "Flag"].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: "0.5rem 0.75rem", fontSize: "0.72rem", fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.recent.map((row) => (
                  <CallRow key={row.ghl_message_id} row={row} />
                ))}
              </tbody>
            </table>
            {data.recent.length === 0 && (
              <div style={{ padding: "1.5rem", textAlign: "center", color: "#94a3b8", fontSize: "0.85rem" }}>
                Nothing to show yet.
              </div>
            )}
          </div>
        </>
      )}
    </PageShell>
  );
}
