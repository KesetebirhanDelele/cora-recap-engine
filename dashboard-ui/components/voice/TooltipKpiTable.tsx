/**
 * TooltipKpiTable — structured tooltip panel used by TrendsChart on hover.
 * Renders a two-column table: KPI label | formatted value.
 */
import type { VoiceTimeSeriesPoint } from "@/types";

interface Props {
  point: VoiceTimeSeriesPoint;
  label?: string;
}

function fmt(value: number | null | undefined, kind: "pct" | "sec" | "int"): string {
  if (value === null || value === undefined) return "—";
  if (kind === "pct") return `${value.toFixed(1)}%`;
  if (kind === "sec") return `${value.toFixed(0)} Sec`;
  return String(Math.round(value));
}

const ROWS: { label: string; key: keyof VoiceTimeSeriesPoint; kind: "pct" | "sec" | "int" }[] = [
  { label: "Unique Contacts",   key: "unique_contacts",      kind: "int" },
  { label: "Booked Appts",      key: "booked_appts",         kind: "int" },
  { label: "Calls Per Day",     key: "calls_per_day",        kind: "int" },
  { label: "Call Completion",   key: "completion_rate",      kind: "pct" },
  { label: "Call Duration",     key: "avg_call_duration_sec", kind: "sec" },
  { label: "Pickup Rate",       key: "pickup_rate",          kind: "pct" },
  { label: "Voicemail Rate",    key: "voicemail_rate",       kind: "pct" },
  { label: "Failed Call Rate",  key: "failed_rate",          kind: "pct" },
];

export default function TooltipKpiTable({ point, label }: Props) {
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "0.75rem 1rem",
        boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
        minWidth: 240,
        fontSize: "0.8rem",
      }}
    >
      {label && (
        <div
          style={{
            fontWeight: 700,
            color: "#b45309",
            marginBottom: "0.5rem",
            borderBottom: "1px solid #f1f5f9",
            paddingBottom: "0.35rem",
            fontSize: "0.85rem",
          }}
        >
          Week of {label}
        </div>
      )}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th
              style={{
                textAlign: "left",
                color: "#64748b",
                fontWeight: 600,
                paddingBottom: "0.25rem",
                borderBottom: "1px solid #e2e8f0",
              }}
            >
              KPI
            </th>
            <th
              style={{
                textAlign: "right",
                color: "#64748b",
                fontWeight: 600,
                paddingBottom: "0.25rem",
                borderBottom: "1px solid #e2e8f0",
              }}
            >
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(({ label: kpiLabel, key, kind }) => (
            <tr key={key}>
              <td
                style={{
                  padding: "0.2rem 0",
                  color: "#475569",
                  paddingRight: "1.5rem",
                }}
              >
                {kpiLabel}
              </td>
              <td
                style={{
                  textAlign: "right",
                  fontWeight: 600,
                  color: "#1e293b",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {fmt(point[key] as number | null | undefined, kind)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
