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

function WowBadge({ change }: { change: number | null | undefined }) {
  if (change === null || change === undefined) {
    return <span style={{ color: "#94a3b8", fontSize: "0.65rem" }}>—</span>;
  }
  const up = change >= 0;
  return (
    <span
      style={{
        color: up ? "#16a34a" : "#dc2626",
        fontSize: "0.65rem",
        fontWeight: 700,
        letterSpacing: "-0.01em",
      }}
    >
      {up ? "▲" : "▼"}&nbsp;{Math.abs(change).toFixed(1)}%
    </span>
  );
}

const ITEMS: { label: string; valueFn: (k: VoiceKpis) => string; wowKey: string }[] = [
  { label: "Unique Contacts",      valueFn: (k) => num(k.unique_contacts),       wowKey: "unique_contacts" },
  { label: "Booked Appts",         valueFn: (k) => num(k.booked_appts),          wowKey: "booked_appts" },
  { label: "Calls Per Day",        valueFn: (k) => num(k.calls_per_day, 1),      wowKey: "total_calls" },
  { label: "Call Completion Rate", valueFn: (k) => pct(k.completion_rate),       wowKey: "completion_rate" },
  { label: "Call Duration (Sec)",  valueFn: (k) => num(k.avg_call_duration_sec), wowKey: "avg_call_duration_sec" },
  { label: "Pickup Rate",          valueFn: (k) => pct(k.pickup_rate),           wowKey: "pickup_rate" },
  { label: "Voicemail Rate",       valueFn: (k) => pct(k.voicemail_rate),        wowKey: "voicemail_rate" },
  { label: "Failed Rate",          valueFn: (k) => pct(k.failed_rate),           wowKey: "failed_rate" },
];

export default function KpiSidebar({ kpis, wowChanges }: Props) {
  return (
    /*
     * Outer container fills the card wrapper height entirely.
     * flex-column so cards distribute evenly.
     */
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: 4,
      }}
    >
      {/* Section label */}
      <div
        style={{
          fontSize: "0.6rem",
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

      {/* Cards — flex:1 makes each card share remaining height equally */}
      {ITEMS.map(({ label, valueFn, wowKey }) => (
        <div
          key={label}
          style={{
            flex: 1,
            minHeight: 0,
            background: "#fffbf0",
            border: "1px solid #fde68a",
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
              fontSize: "0.58rem",
              fontWeight: 700,
              color: "#b45309",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              lineHeight: 1.2,
              marginBottom: 2,
            }}
          >
            {label}
          </div>

          {/* Value + WoW row */}
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: "0.25rem",
            }}
          >
            <span
              style={{
                fontSize: "1.15rem",
                fontWeight: 700,
                color: "#1e293b",
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
      ))}
    </div>
  );
}
