"use client";

import type { LeadTraceResponse, TraceStep } from "@/types";

const STATUS_COLORS: Record<string, string> = {
  completed: "#16a34a",
  failed:    "#dc2626",
  running:   "#2563eb",
  pending:   "#64748b",
  cancelled: "#94a3b8",
  claimed:   "#7c3aed",
};

function StepRow({ step }: { step: TraceStep }) {
  const color = STATUS_COLORS[step.status] ?? "#64748b";
  const dur = step.duration_ms !== null ? `${step.duration_ms}ms` : "—";

  return (
    <div
      style={{
        background: "#ffffff",
        border: `1px solid ${color}30`,
        borderLeft: `3px solid ${color}`,
        borderRadius: 6,
        padding: "0.75rem 1rem",
        marginBottom: "0.5rem",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 700, fontSize: "0.875rem", color: "#0f172a" }}>{step.job_type}</span>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          {step.is_shadow && (
            <span style={{ fontSize: "0.7rem", background: "#ede9fe", color: "#7c3aed", padding: "1px 6px", borderRadius: 4, fontWeight: 600 }}>
              shadow
            </span>
          )}
          <span style={{ color, fontSize: "0.8rem", fontWeight: 600 }}>{step.status}</span>
          <span style={{ color: "#94a3b8", fontSize: "0.75rem" }}>{dur}</span>
        </div>
      </div>

      {step.failure_reason && (
        <p style={{ color: "#dc2626", fontSize: "0.8rem", marginTop: 4, marginBottom: 0 }}>
          {step.failure_reason}
        </p>
      )}

      {step.is_shadow && step.shadow_payload && (
        <details style={{ marginTop: 4 }}>
          <summary style={{ fontSize: "0.75rem", color: "#64748b", cursor: "pointer" }}>
            Shadow payload
          </summary>
          <pre
            style={{
              fontSize: "0.75rem",
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
              padding: "0.5rem",
              borderRadius: 4,
              marginTop: 4,
              overflowX: "auto",
              color: "#374151",
            }}
          >
            {JSON.stringify(step.shadow_payload, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

interface Props {
  trace: LeadTraceResponse;
}

export default function PipelineTrace({ trace }: Props) {
  return (
    <div>
      <div
        style={{
          background: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "1rem",
          marginBottom: "1.5rem",
          display: "flex",
          gap: "2rem",
          flexWrap: "wrap",
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        }}
      >
        {[
          { label: "Campaign", value: trace.campaign_name ?? "—" },
          { label: "Status",   value: trace.status },
          { label: "Phone",    value: trace.normalized_phone ?? "—" },
          { label: "Steps",    value: String(trace.steps.length) },
        ].map(({ label, value }) => (
          <div key={label}>
            <span style={{ color: "#64748b", fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.06em", display: "block" }}>{label}</span>
            <div style={{ fontWeight: 700, color: "#0f172a", marginTop: 2 }}>{value}</div>
          </div>
        ))}
      </div>
      {trace.steps.map((s) => (
        <StepRow key={s.job_id} step={s} />
      ))}
    </div>
  );
}
