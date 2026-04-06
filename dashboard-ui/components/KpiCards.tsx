"use client";

import type { MetricsResponse } from "@/types";

interface Props {
  kpis: MetricsResponse["kpis"];
  period: MetricsResponse["period"];
}

const ACCENT: Record<string, string> = {
  "Total Calls":   "#3b82f6",
  "Pickup Rate":   "#16a34a",
  "Voicemail Rate":"#d97706",
  "Failed Rate":   "#dc2626",
  "Enrolled":      "#7c3aed",
  "DNC Rate":      "#ea580c",
};

function Card({ label, value }: { label: string; value: string }) {
  const accent = ACCENT[label] ?? "#3b82f6";
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderTop: `2px solid ${accent}`,
        borderRadius: 8,
        padding: "1rem 1.25rem",
        flex: "1 1 150px",
        boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
      }}
    >
      <div style={{ fontSize: "0.72rem", color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.5rem", fontWeight: 800, color: "#0f172a" }}>{value}</div>
    </div>
  );
}

const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);

export default function KpiCards({ kpis, period }: Props) {
  return (
    <div>
      <p style={{ fontSize: "0.75rem", color: "#94a3b8", marginBottom: "1rem" }}>
        {new Date(period.from).toLocaleDateString()} – {new Date(period.to).toLocaleDateString()}
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: "0.875rem" }}>
        <Card label="Total Calls"   value={String(kpis.total_calls)} />
        <Card label="Pickup Rate"   value={pct(kpis.pickup_rate)} />
        <Card label="Voicemail Rate" value={pct(kpis.voicemail_rate)} />
        <Card label="Failed Rate"   value={pct(kpis.failed_rate)} />
        <Card label="Enrolled"      value={String(kpis.enrolled_count)} />
        <Card label="DNC Rate"      value={pct(kpis.do_not_call_rate)} />
      </div>
    </div>
  );
}
