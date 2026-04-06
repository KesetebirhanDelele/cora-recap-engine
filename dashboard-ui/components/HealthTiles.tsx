"use client";

import type { HealthResponse } from "@/types";

interface Props {
  health: HealthResponse;
}

function Tile({
  label,
  value,
  critical,
  warn,
}: {
  label: string;
  value: string | number;
  critical?: boolean;
  warn?: boolean;
}) {
  const bg = critical ? "#450a0a" : warn ? "#431407" : "#1e293b";
  const border = critical ? "#dc2626" : warn ? "#ea580c" : "#475569";
  const valueColor = critical ? "#fca5a5" : warn ? "#fdba74" : "#ffffff";

  return (
    <div
      style={{
        background: bg,
        border: `1px solid ${border}`,
        borderRadius: 8,
        padding: "0.875rem 1.25rem",
        minWidth: 150,
        flex: "1 1 150px",
      }}
    >
      <div style={{ fontSize: "0.7rem", color: "#94a3b8", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
      <div style={{ fontSize: "1.4rem", fontWeight: "700", color: valueColor, letterSpacing: "-0.01em" }}>
        {value}
      </div>
    </div>
  );
}

export default function HealthTiles({ health }: Props) {
  const lag = health.queue_lag_seconds;
  const errorRate = health.error_rate;

  return (
    <div style={{ display: "flex", gap: "0.625rem", flexWrap: "wrap" }}>
      <Tile label="Queue Lag"       value={`${lag.toFixed(0)}s`}    critical={lag > 300} warn={lag > 60} />
      <Tile label="Active Workers"  value={health.active_workers}    critical={health.active_workers === 0} />
      <Tile label="Open Exceptions" value={health.open_exception_count} critical={health.open_exception_count >= 10} warn={health.open_exception_count > 0} />
      <Tile label="Stuck Jobs"      value={health.stuck_job_count}   warn={health.stuck_job_count > 0} />
      <Tile label="Expired Leases"  value={health.expired_lease_count} warn={health.expired_lease_count > 0} />
      <Tile label="Completed (5m)"  value={health.jobs_completed_last_5m} />
      <Tile label="Failed (5m)"     value={health.jobs_failed_last_5m} warn={health.jobs_failed_last_5m > 0} />
      <Tile
        label="Error Rate"
        value={errorRate !== null ? `${(errorRate * 100).toFixed(1)}%` : "—"}
        critical={errorRate !== null && errorRate > 0.2}
        warn={errorRate !== null && errorRate > 0.05}
      />
      <Tile label="Mode"      value={health.shadow_mode_enabled ? "Shadow" : "Live"} />
      <Tile label="GHL Write" value={health.ghl_write_mode} />
    </div>
  );
}
