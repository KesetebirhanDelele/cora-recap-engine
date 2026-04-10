/**
 * TooltipKpiTable — structured tooltip panel used by TrendsChart on hover.
 * Shows campaign call breakdown (with bar colors) then rate metrics (with line colors).
 * When hovering a bar segment, all KPIs recompute for that campaign only.
 */
import type { VoiceTimeSeriesPoint, CampaignWeekStats } from "@/types";

interface Props {
  point: VoiceTimeSeriesPoint;
  label?: string;
  /** When set, show a focused single-series tooltip instead of the full table. */
  focusSeries?: string | null;
}

function fmt(value: number | null | undefined, kind: "pct" | "sec" | "int"): string {
  if (value === null || value === undefined) return "—";
  if (kind === "pct") return `${value.toFixed(1)}%`;
  if (kind === "sec") return `${value.toFixed(0)}s`;
  return String(Math.round(value));
}

// Bar colors — must match TrendsChart bar fills exactly
const BAR_ROWS: { label: string; key: keyof VoiceTimeSeriesPoint; color: string }[] = [
  { label: "Cold Lead", key: "cold",     color: "#2563eb" },
  { label: "Inbound",   key: "inbound",  color: "#0891b2" },
  { label: "New Lead",  key: "new_lead", color: "#16a34a" },
];

// Line colors — must match TrendsChart line strokes exactly
const LINE_ROWS: { label: string; key: keyof VoiceTimeSeriesPoint; color: string }[] = [
  { label: "Completion %",   key: "completion_rate",      color: "#0ea5e9" },
  { label: "Pickup %",       key: "pickup_rate",          color: "#10b981" },
  { label: "Voicemail %",    key: "voicemail_rate",       color: "#8b5cf6" },
  { label: "Failed %",       key: "failed_rate",          color: "#ef4444" },
  { label: "Booking Rate %", key: "booking_rate",         color: "#7c3aed" },
];

// Plain KPIs with no chart color mapping
const PLAIN_ROWS: { label: string; key: keyof VoiceTimeSeriesPoint; kind: "pct" | "sec" | "int" }[] = [
  { label: "Unique Contacts", key: "unique_contacts",       kind: "int" },
  { label: "Booked Appts",    key: "booked_appts",          kind: "int" },
  { label: "Calls / Day",     key: "calls_per_day",         kind: "int" },
  { label: "Avg Duration",    key: "avg_call_duration_sec", kind: "sec" },
];

function Swatch({ color }: { color: string }) {
  return (
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
  );
}

const TD_LABEL: React.CSSProperties = { padding: "0.18rem 0", color: "#475569", paddingRight: "1.25rem", display: "flex", alignItems: "center" };
const TD_VALUE: React.CSSProperties = { textAlign: "right", fontWeight: 600, color: "#1e293b", fontVariantNumeric: "tabular-nums", padding: "0.18rem 0" };
const DIVIDER: React.CSSProperties = { borderTop: "1px solid #f1f5f9", margin: "0.35rem 0" };

// ── Focused single-series tooltip ────────────────────────────────────────────

function FocusedTooltip({ point, label, focusSeries }: Required<Pick<Props, "point" | "focusSeries">> & { label?: string }) {
  const barEntry  = BAR_ROWS.find((r) => r.key === focusSeries);
  const lineEntry = LINE_ROWS.find((r) => r.key === focusSeries);

  const entry = barEntry ?? lineEntry;
  if (!entry) return null;

  const isBar   = !!barEntry;
  const value   = point[entry.key as keyof VoiceTimeSeriesPoint] as number | null | undefined;
  const display = isBar ? fmt(value, "int") : fmt(value, "pct");
  const color   = entry.color;

  return (
    <div
      style={{
        background: "#ffffff",
        border: `1px solid ${color}40`,
        borderLeft: `3px solid ${color}`,
        borderRadius: 8,
        padding: "0.5rem 0.75rem",
        boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
        minWidth: 170,
        fontSize: "0.78rem",
      }}
    >
      {label && (
        <div style={{ fontWeight: 700, color: "#b45309", marginBottom: "0.35rem", fontSize: "0.75rem" }}>
          Week of {label}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
        {isBar ? (
          <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: color, flexShrink: 0 }} />
        ) : (
          <span style={{ display: "inline-block", width: 14, height: 2, borderRadius: 1, background: color, flexShrink: 0 }} />
        )}
        <span style={{ color: "#475569", flex: 1 }}>{entry.label}</span>
        <span style={{ fontWeight: 800, color, fontSize: "1.05rem", fontVariantNumeric: "tabular-nums" }}>
          {display}
        </span>
      </div>
      {/* Secondary context line */}
      {isBar && (
        <div style={{ marginTop: "0.3rem", fontSize: "0.7rem", color: "#94a3b8", paddingLeft: 16 }}>
          Total calls this week: {fmt(
            (point.cold ?? 0) + (point.inbound ?? 0) + (point.new_lead ?? 0),
            "int"
          )}
        </div>
      )}
      {!isBar && (
        <div style={{ marginTop: "0.3rem", fontSize: "0.7rem", color: "#94a3b8", paddingLeft: 16 }}>
          {fmt(point.unique_contacts, "int")} unique contacts
        </div>
      )}
    </div>
  );
}

// Map bar dataKey → the stats field name on VoiceTimeSeriesPoint
const BAR_STATS_KEY: Record<string, keyof VoiceTimeSeriesPoint> = {
  cold:     "cold_stats",
  inbound:  "inbound_stats",
  new_lead: "new_lead_stats",
};
const BAR_KEYS = new Set(Object.keys(BAR_STATS_KEY));

export default function TooltipKpiTable({ point, label, focusSeries }: Props) {
  // Line hover → focused single-metric card (unchanged)
  if (focusSeries && !BAR_KEYS.has(focusSeries)) {
    return <FocusedTooltip point={point} label={label} focusSeries={focusSeries} />;
  }

  // Resolve per-campaign stats when a bar is hovered; fall back to week aggregate
  const campStats: CampaignWeekStats | null = focusSeries
    ? (point[BAR_STATS_KEY[focusSeries]] as CampaignWeekStats | undefined) ?? null
    : null;

  // Helper: read from campaign stats when available, else from the week point
  function val(campKey: keyof CampaignWeekStats, weekKey: keyof VoiceTimeSeriesPoint, kind: "pct" | "sec" | "int"): string {
    const v = campStats ? campStats[campKey] : point[weekKey];
    return fmt(v as number | null | undefined, kind);
  }

  const highlightColor = focusSeries ? BAR_ROWS.find((r) => r.key === focusSeries)?.color : undefined;

  return (
    <div
      style={{
        background: "#ffffff",
        border: highlightColor ? `1px solid ${highlightColor}40` : "1px solid #e2e8f0",
        borderTop: highlightColor ? `2px solid ${highlightColor}` : "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "0.625rem 0.875rem",
        boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
        minWidth: 230,
        fontSize: "0.78rem",
      }}
    >
      {/* Header */}
      {label && (
        <div style={{ fontWeight: 700, color: "#b45309", marginBottom: "0.4rem", borderBottom: "1px solid #f1f5f9", paddingBottom: "0.3rem", fontSize: "0.82rem" }}>
          Week of {label}
          {campStats && highlightColor && (
            <span style={{ marginLeft: 8, fontSize: "0.72rem", fontWeight: 600, color: highlightColor }}>
              — {BAR_ROWS.find((r) => r.key === focusSeries)?.label} only
            </span>
          )}
        </div>
      )}

      {/* Voice Agent bar breakdown — always shows all three counts */}
      <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "0.25rem" }}>
        Calls by voice agent
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {BAR_ROWS.map(({ label: l, key, color }) => {
            const isActive = focusSeries === key;
            return (
              <tr key={key} style={{ background: isActive ? `${color}12` : "transparent" }}>
                <td style={{ ...TD_LABEL, fontWeight: isActive ? 700 : undefined, color: isActive ? color : "#475569" }}>
                  <Swatch color={color} />{l}
                </td>
                <td style={{ ...TD_VALUE, color: isActive ? color : "#1e293b", fontSize: isActive ? "1rem" : undefined }}>
                  {fmt(point[key] as number | null | undefined, "int")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div style={DIVIDER} />

      {/* Rate lines — values come from campaign stats when a bar is hovered */}
      <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "0.25rem" }}>
        Rates {campStats && <span style={{ color: highlightColor, fontWeight: 400 }}>(voice agent)</span>}
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {LINE_ROWS.map(({ label: l, key, color }) => (
            <tr key={key}>
              <td style={TD_LABEL}>
                <span style={{ display: "inline-block", width: 12, height: 2, background: color, marginRight: 6, borderRadius: 1, flexShrink: 0 }} />
                {l}
              </td>
              <td style={TD_VALUE}>
                {val(key as keyof CampaignWeekStats, key as keyof VoiceTimeSeriesPoint, "pct")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={DIVIDER} />

      {/* Plain KPIs — values come from campaign stats when a bar is hovered */}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          <tr>
            <td style={{ ...TD_LABEL, color: "#64748b" }}>Unique Contacts</td>
            <td style={TD_VALUE}>{val("unique_contacts", "unique_contacts", "int")}</td>
          </tr>
          <tr>
            <td style={{ ...TD_LABEL, color: "#64748b" }}>Booked Appts</td>
            <td style={TD_VALUE}>{val("booked_appts", "booked_appts", "int")}</td>
          </tr>
          <tr>
            <td style={{ ...TD_LABEL, color: "#64748b" }}>Calls / Day</td>
            <td style={TD_VALUE}>{val("calls_per_day", "calls_per_day", "int")}</td>
          </tr>
          <tr>
            <td style={{ ...TD_LABEL, color: "#64748b" }}>Avg Duration</td>
            <td style={TD_VALUE}>{val("avg_call_duration_sec", "avg_call_duration_sec", "sec")}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
