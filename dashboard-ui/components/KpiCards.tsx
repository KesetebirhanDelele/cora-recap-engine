"use client";

import type { MetricsResponse } from "@/types";

interface Props {
  kpis: MetricsResponse["kpis"];
  period: MetricsResponse["period"];
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "#1e293b",
        border: "1px solid #334155",
        borderRadius: 8,
        padding: "0.875rem 1.25rem",
        minWidth: 140,
      }}
    >
      <div style={{ fontSize: "0.75rem", color: "#94a3b8", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: "1.25rem", fontWeight: "bold" }}>{value}</div>
    </div>
  );
}

const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);

export default function KpiCards({ kpis, period }: Props) {
  return (
    <div>
      <p style={{ fontSize: "0.8rem", color: "#64748b", marginBottom: "1rem" }}>
        {new Date(period.from).toLocaleDateString()} – {new Date(period.to).toLocaleDateString()}
      </p>
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <Card label="Total Calls" value={String(kpis.total_calls)} />
        <Card label="Pickup Rate" value={pct(kpis.pickup_rate)} />
        <Card label="Voicemail Rate" value={pct(kpis.voicemail_rate)} />
        <Card label="Failed Rate" value={pct(kpis.failed_rate)} />
        <Card label="Enrolled" value={String(kpis.enrolled_count)} />
        <Card label="DNC Rate" value={pct(kpis.do_not_call_rate)} />
      </div>
    </div>
  );
}
