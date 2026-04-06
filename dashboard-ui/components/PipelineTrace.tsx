"use client";

import type { LeadTraceResponse, TraceStep } from "@/types";

const STATUS_COLORS: Record<string, string> = {
  completed: "#22c55e",
  failed: "#ef4444",
  running: "#3b82f6",
  pending: "#94a3b8",
  cancelled: "#64748b",
  claimed: "#a855f7",
};

function StepRow({ step }: { step: TraceStep }) {
  const color = STATUS_COLORS[step.status] ?? "#94a3b8";
  const dur = step.duration_ms !== null ? `${step.duration_ms}ms` : "—";

  return (
    <div
      style={{
        background: "#1e293b",
        border: `1px solid ${color}44`,
        borderRadius: 6,
        padding: "0.75rem 1rem",
        marginBottom: "0.5rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: "bold", fontSize: "0.875rem" }}>{step.job_type}</span>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          {step.is_shadow && (
            <span style={{ fontSize: "0.7rem", background: "#7c3aed22", color: "#a78bfa", padding: "1px 6px", borderRadius: 4 }}>
              shadow
            </span>
          )}
          <span style={{ color, fontSize: "0.8rem" }}>{step.status}</span>
          <span style={{ color: "#64748b", fontSize: "0.75rem" }}>{dur}</span>
        </div>
      </div>

      {step.failure_reason && (
        <p style={{ color: "#f87171", fontSize: "0.8rem", marginTop: 4, marginBottom: 0 }}>
          {step.failure_reason}
        </p>
      )}

      {step.is_shadow && step.shadow_payload && (
        <details style={{ marginTop: 4 }}>
          <summary style={{ fontSize: "0.75rem", color: "#94a3b8", cursor: "pointer" }}>
            Shadow payload
          </summary>
          <pre
            style={{
              fontSize: "0.75rem",
              background: "#0f172a",
              padding: "0.5rem",
              borderRadius: 4,
              marginTop: 4,
              overflowX: "auto",
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
          background: "#1e293b",
          borderRadius: 8,
          padding: "1rem",
          marginBottom: "1.5rem",
          display: "flex",
          gap: "2rem",
          flexWrap: "wrap",
        }}
      >
        <div>
          <span style={{ color: "#64748b", fontSize: "0.75rem" }}>Campaign</span>
          <div style={{ fontWeight: "bold" }}>{trace.campaign_name ?? "—"}</div>
        </div>
        <div>
          <span style={{ color: "#64748b", fontSize: "0.75rem" }}>Status</span>
          <div style={{ fontWeight: "bold" }}>{trace.status}</div>
        </div>
        <div>
          <span style={{ color: "#64748b", fontSize: "0.75rem" }}>Phone</span>
          <div style={{ fontWeight: "bold" }}>{trace.normalized_phone ?? "—"}</div>
        </div>
        <div>
          <span style={{ color: "#64748b", fontSize: "0.75rem" }}>Steps</span>
          <div style={{ fontWeight: "bold" }}>{trace.steps.length}</div>
        </div>
      </div>
      {trace.steps.map((s) => (
        <StepRow key={s.job_id} step={s} />
      ))}
    </div>
  );
}
