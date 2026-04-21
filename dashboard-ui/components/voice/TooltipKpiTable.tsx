/**
 * TooltipKpiTable — structured tooltip matching the Power BI format.
 *
 * Layout:
 *   Week of <date>
 *   KPI                  │  Tooltip KPI Value
 *   ─────────────────────┤────────────────────
 *   Unique Contacts      │  nnn
 *   Reached              │  nnn
 *   Booked Appts         │  nnn
 *   Calls Per Day        │  nnn
 *   ─────────────────────┼────────────────────
 *   Call Completion Rate │  nn%
 *   Booking Rate         │  nn%
 *   Pickup Rate          │  nn%
 *   Voicemail            │  nn%
 *   Failed Call Rate     │  nn%
 *   ─────────────────────┼────────────────────
 *   Call Type
 *   ● Cold Lead          │  nnn
 *   ● Inbound            │  nnn
 *   ● New Lead           │  nnn
 *
 * When a bar segment is hovered (focusSeries = "cold" | "inbound" | "new_lead"),
 * the KPI values reflect that campaign's stats only.
 */
import type { VoiceTimeSeriesPoint, CampaignWeekStats } from "@/types";

interface Props {
  point: VoiceTimeSeriesPoint;
  label?: string;
  focusSeries?: string | null;
}

function fmt(value: number | null | undefined, kind: "pct" | "sec" | "int"): string {
  if (value === null || value === undefined) return "—";
  if (kind === "pct") return `${value.toFixed(1)}%`;
  if (kind === "sec") return `${value.toFixed(0)}s`;
  return Math.round(value).toLocaleString();
}

const BAR_ROWS = [
  { label: "Cold Lead", key: "cold",     color: "#2563eb" },
  { label: "Inbound",   key: "inbound",  color: "#eab308" },
  { label: "New Lead",  key: "new_lead", color: "#16a34a" },
] as const;

const BAR_STATS_KEY: Record<string, keyof VoiceTimeSeriesPoint> = {
  cold:     "cold_stats",
  inbound:  "inbound_stats",
  new_lead: "new_lead_stats",
};
const BAR_KEYS = new Set(Object.keys(BAR_STATS_KEY));

const LINE_META = [
  { label: "Call Completion Rate", campKey: "completion_rate" as keyof CampaignWeekStats, weekKey: "completion_rate" as keyof VoiceTimeSeriesPoint, color: "#0ea5e9" },
  { label: "Booking Rate",         campKey: "booking_rate"    as keyof CampaignWeekStats, weekKey: "booking_rate"    as keyof VoiceTimeSeriesPoint, color: "#7c3aed" },
  { label: "Pickup Rate",          campKey: "pickup_rate"     as keyof CampaignWeekStats, weekKey: "pickup_rate"     as keyof VoiceTimeSeriesPoint, color: "#10b981" },
  { label: "Voicemail",            campKey: "voicemail_rate"  as keyof CampaignWeekStats, weekKey: "voicemail_rate"  as keyof VoiceTimeSeriesPoint, color: "#8b5cf6" },
  { label: "Failed Call Rate",     campKey: "failed_rate"     as keyof CampaignWeekStats, weekKey: "failed_rate"     as keyof VoiceTimeSeriesPoint, color: "#ef4444" },
] as const;

// td styles — display:flex makes the cell contents flex-arranged (works in modern browsers)
const TDL: React.CSSProperties = {
  padding: "0.18rem 1rem 0.18rem 0",
  color: "#475569",
  fontSize: "0.76rem",
  display: "flex",
  alignItems: "center",
  whiteSpace: "nowrap",
};
const TDR: React.CSSProperties = {
  textAlign: "right",
  fontWeight: 700,
  color: "#1e293b",
  fontSize: "0.76rem",
  fontVariantNumeric: "tabular-nums",
  padding: "0.18rem 0",
};
const DIVIDER: React.CSSProperties = {
  borderTop: "1px solid #f1f5f9",
  margin: "0.3rem 0",
};
const COL_HEAD: React.CSSProperties = {
  fontWeight: 700,
  color: "#94a3b8",
  fontSize: "0.68rem",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  padding: "0 0 0.25rem 0",
};

export default function TooltipKpiTable({ point, label, focusSeries }: Props) {
  const campStats: CampaignWeekStats | null =
    focusSeries && BAR_KEYS.has(focusSeries)
      ? (point[BAR_STATS_KEY[focusSeries]] as CampaignWeekStats | undefined) ?? null
      : null;

  const highlightColor = BAR_ROWS.find((r) => r.key === focusSeries)?.color;
  const highlightLabel = BAR_ROWS.find((r) => r.key === focusSeries)?.label;

  // Total calls for the active scope (full week or single campaign)
  const totalCalls =
    campStats?.total_calls ??
    ((point.cold ?? 0) + (point.inbound ?? 0) + (point.new_lead ?? 0));

  // Reached = calls where someone actually picked up (completed)
  const compRate = campStats ? campStats.completion_rate : point.completion_rate;
  const reached = compRate != null ? Math.round(totalCalls * compRate / 100) : null;

  // Read a value from campStats (if focused) or the week aggregate
  function v(
    campKey: keyof CampaignWeekStats,
    weekKey: keyof VoiceTimeSeriesPoint,
    kind: "pct" | "int" | "sec"
  ): string {
    const val = campStats ? campStats[campKey] : point[weekKey];
    return fmt(val as number | null | undefined, kind);
  }

  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderTop: `3px solid ${highlightColor ?? "#b45309"}`,
        borderRadius: 8,
        padding: "0.55rem 0.85rem 0.5rem",
        boxShadow: "0 4px 16px rgba(0,0,0,0.14)",
        minWidth: 256,
        fontSize: "0.76rem",
      }}
    >
      {/* ── Date / scope header ─────────────────────────────────────── */}
      {label && (
        <div
          style={{
            fontWeight: 700,
            color: "#b45309",
            fontSize: "0.8rem",
            marginBottom: "0.35rem",
            display: "flex",
            alignItems: "baseline",
            gap: "0.5rem",
          }}
        >
          Week of {label}
          {highlightLabel && (
            <span style={{ fontSize: "0.7rem", fontWeight: 600, color: highlightColor }}>
              — {highlightLabel}
            </span>
          )}
        </div>
      )}

      {/* ── Column header row ───────────────────────────────────────── */}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ ...COL_HEAD, textAlign: "left" }}>KPI</th>
            <th style={{ ...COL_HEAD, textAlign: "right" }}>Tooltip KPI Value</th>
          </tr>
        </thead>

        {/* ── Count KPIs ──────────────────────────────────────────────── */}
        <tbody>
          <tr>
            <td style={TDL}>Unique Contacts</td>
            <td style={TDR}>{v("unique_contacts", "unique_contacts", "int")}</td>
          </tr>
          <tr>
            <td style={TDL}>Reached</td>
            <td style={TDR}>{reached != null ? reached.toLocaleString() : "—"}</td>
          </tr>
          <tr>
            <td style={TDL}>Booked Appts</td>
            <td style={TDR}>{v("booked_appts", "booked_appts", "int")}</td>
          </tr>
          <tr>
            <td style={TDL}>Calls Per Day</td>
            <td style={TDR}>{v("calls_per_day", "calls_per_day", "int")}</td>
          </tr>
        </tbody>
      </table>

      <div style={DIVIDER} />

      {/* ── Rate KPIs ───────────────────────────────────────────────── */}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {LINE_META.map(({ label: l, campKey, weekKey, color }) => (
            <tr key={l}>
              <td style={TDL}>
                <span
                  style={{
                    display: "inline-block",
                    width: 12,
                    height: 2,
                    background: color,
                    marginRight: 6,
                    borderRadius: 1,
                    flexShrink: 0,
                  }}
                />
                {l}
              </td>
              <td style={{ ...TDR, color }}>{v(campKey, weekKey, "pct")}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={DIVIDER} />

      {/* ── Call Type breakdown ─────────────────────────────────────── */}
      <div
        style={{
          fontWeight: 700,
          color: "#94a3b8",
          fontSize: "0.68rem",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: "0.2rem",
        }}
      >
        Call Type
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {BAR_ROWS.map(({ label: l, key, color }) => {
            const isActive = focusSeries === key;
            return (
              <tr
                key={key}
                style={{ background: isActive ? `${color}14` : "transparent" }}
              >
                <td
                  style={{
                    ...TDL,
                    color: isActive ? color : "#475569",
                    fontWeight: isActive ? 700 : undefined,
                  }}
                >
                  <span
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: 2,
                      background: color,
                      marginRight: 6,
                      flexShrink: 0,
                    }}
                  />
                  {l}
                </td>
                <td style={{ ...TDR, color: isActive ? color : "#1e293b" }}>
                  {fmt(point[key] as number | null | undefined, "int")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
