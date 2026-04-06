"use client";

/**
 * KpiSidebar — 8 KPI tiles that fill the full sidebar height evenly.
 * Each card uses flex:1 so they distribute height automatically.
 * Font sizes and padding are kept compact for single-screen fit.
 */

import type { VoiceKpis } from "@/types";

interface Props {
  kpis: VoiceKpis;
  wowChanges: Record<string, number | null>;
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function num(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(decimals);
}

// Colors must match the line strokes in TrendsChart exactly.
const LINE_COLOR: Record<string, string> = {
  completion_rate:      "#0ea5e9",
  pickup_rate:          "#10b981",
  voicemail_rate:       "#8b5cf6",
  failed_rate:          "#ef4444",
  booking_rate:         "#7c3aed",
};

function WowBadge({ change }: { change: number | null | undefined }) {
  if (change === null || change === undefined) {
    return <span style={{ color: "#94a3b8", fontSize: "0.8rem" }}>—</span>;
  }
  const up = change >= 0;
  return (
    <span
      style={{
        color: up ? "#16a34a" : "#dc2626",
        fontSize: "0.8rem",
        fontWeight: 700,
        letterSpacing: "-0.01em",
      }}
    >
      {up ? "▲" : "▼"}&nbsp;{Math.abs(change).toFixed(1)}%
    </span>
  );
}

const ITEMS: { label: string; valueFn: (k: VoiceKpis) => string; wowKey: string; colorKey?: string }[] = [
  { label: "Unique Contacts",      valueFn: (k) => num(k.unique_contacts),       wowKey: "unique_contacts" },
  { label: "Booked Appts",         valueFn: (k) => num(k.booked_appts),          wowKey: "booked_appts" },
  { label: "Calls Per Day",        valueFn: (k) => num(k.calls_per_day, 1),      wowKey: "total_calls" },
  { label: "Call Completion Rate", valueFn: (k) => pct(k.completion_rate),       wowKey: "completion_rate",      colorKey: "completion_rate" },
  { label: "Call Duration (Sec)",  valueFn: (k) => num(k.avg_call_duration_sec), wowKey: "avg_call_duration_sec" },
  { label: "Pickup Rate",          valueFn: (k) => pct(k.pickup_rate),           wowKey: "pickup_rate",          colorKey: "pickup_rate" },
  { label: "Voicemail Rate",       valueFn: (k) => pct(k.voicemail_rate),        wowKey: "voicemail_rate",       colorKey: "voicemail_rate" },
  { label: "Failed Rate",          valueFn: (k) => pct(k.failed_rate),           wowKey: "failed_rate",          colorKey: "failed_rate" },
  { label: "Booking Rate",         valueFn: (k) => pct(k.booking_rate),          wowKey: "booking_rate",         colorKey: "booking_rate" },
];

export default function KpiSidebar({ kpis, wowChanges }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 4 }}>
      {/* Section label */}
      <div
        style={{
          fontSize: "0.75rem",
          fontWeight: 700,
          color: "#b45309",
          textTransform: "uppercase",
          letterSpacing: "0.09em",
          flexShrink: 0,
          paddingBottom: 2,
          borderBottom: "1px solid #fde68a",
          marginBottom: 2,
        }}
      >
        Performance KPIs
      </div>

      {/* Cards — flex:1 distributes height equally */}
      {ITEMS.map(({ label, valueFn, wowKey, colorKey }) => {
        const accent = colorKey ? LINE_COLOR[colorKey] : undefined;
        return (
          <div
            key={label}
            style={{
              flex: 1,
              minHeight: 0,
              background: accent ? `${accent}0a` : "#fffbf0",
              border: `1px solid ${accent ? `${accent}40` : "#fde68a"}`,
              borderLeft: `3px solid ${accent ?? "#f59e0b"}`,
              borderRadius: 6,
              padding: "0.3rem 0.6rem",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
            }}
          >
            {/* Label row */}
            <div
              style={{
                fontSize: "0.73rem",
                fontWeight: 700,
                color: accent ?? "#b45309",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                lineHeight: 1.2,
                marginBottom: 2,
              }}
            >
              {label}
            </div>

            {/* Value + WoW row */}
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "0.25rem" }}>
              <span
                style={{
                  fontSize: "1.4rem",
                  fontWeight: 700,
                  color: accent ?? "#1e293b",
                  letterSpacing: "-0.02em",
                  lineHeight: 1,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {valueFn(kpis)}
              </span>
              <WowBadge change={wowChanges[wowKey]} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
