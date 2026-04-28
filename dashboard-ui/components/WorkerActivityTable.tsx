"use client";

import type { WorkerActivityResponse, WorkerSummary } from "@/types";

const thStyle: React.CSSProperties = {
  padding: "0.5rem 0.75rem",
  fontSize: "0.7rem",
  fontWeight: 700,
  color: "#64748b",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  textAlign: "left",
  borderBottom: "1px solid #e2e8f0",
};

const tdStyle: React.CSSProperties = {
  padding: "0.625rem 0.75rem",
  fontSize: "0.8rem",
  color: "#374151",
  borderBottom: "1px solid #f1f5f9",
  verticalAlign: "top",
};

function StatusDot({ active }: { active: boolean }) {
  return (
    <span style={{
      display: "inline-block",
      width: 8,
      height: 8,
      borderRadius: "50%",
      background: active ? "#16a34a" : "#94a3b8",
      marginRight: "0.4rem",
      flexShrink: 0,
    }} />
  );
}

function WorkerRow({ w }: { w: WorkerSummary }) {
  return (
    <tr>
      <td style={tdStyle}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <StatusDot active={w.is_active} />
          <span style={{ fontFamily: "monospace", fontSize: "0.75rem", color: "#1e293b" }}>
            {w.worker_id_short}
          </span>
        </div>
        <div style={{ fontSize: "0.65rem", color: "#94a3b8", marginTop: 2, paddingLeft: 16 }}>
          {w.worker_id}
        </div>
      </td>
      <td style={tdStyle}>
        {w.is_active && w.current_job_type ? (
          <span style={{
            background: "#eff6ff",
            color: "#1d4ed8",
            border: "1px solid #bfdbfe",
            borderRadius: 4,
            padding: "0.15rem 0.5rem",
            fontSize: "0.7rem",
            fontWeight: 600,
            whiteSpace: "nowrap",
          }}>
            {w.current_job_type}
          </span>
        ) : (
          <span style={{ color: "#94a3b8", fontSize: "0.75rem" }}>idle</span>
        )}
      </td>
      <td style={{ ...tdStyle, fontWeight: 700, color: w.jobs_last_10m > 0 ? "#0f172a" : "#94a3b8" }}>
        {w.jobs_last_10m}
      </td>
      <td style={{ ...tdStyle, color: "#374151" }}>
        {w.avg_duration_s != null ? `${w.avg_duration_s}s` : "—"}
      </td>
      <td style={{ ...tdStyle, padding: "0.5rem 0.75rem" }}>
        {w.breakdown.length === 0 ? (
          <span style={{ color: "#94a3b8", fontSize: "0.75rem" }}>—</span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {w.breakdown.map((b) => (
              <div key={b.job_type} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <span style={{ fontSize: "0.7rem", color: "#475569", minWidth: 180 }}>{b.job_type}</span>
                <span style={{ fontSize: "0.7rem", fontWeight: 600, color: "#0f172a", minWidth: 24 }}>{b.count}</span>
                <span style={{ fontSize: "0.65rem", color: "#94a3b8" }}>
                  {b.avg_duration_s != null ? `avg ${b.avg_duration_s}s` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

interface Props {
  data: WorkerActivityResponse;
}

export default function WorkerActivityTable({ data }: Props) {
  const activeCount = data.workers.filter((w) => w.is_active).length;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.625rem", marginBottom: "0.875rem" }}>
        <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0f172a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Worker Activity
        </span>
        <span style={{
          background: activeCount > 0 ? "#f0fdf4" : "#f1f5f9",
          color: activeCount > 0 ? "#16a34a" : "#94a3b8",
          border: `1px solid ${activeCount > 0 ? "#bbf7d0" : "#e2e8f0"}`,
          borderRadius: 99,
          padding: "0 0.5rem",
          fontSize: "0.7rem",
          fontWeight: 700,
          lineHeight: "1.5rem",
        }}>
          {activeCount} active / {data.workers.length} seen
        </span>
        <span style={{ fontSize: "0.65rem", color: "#94a3b8", marginLeft: "auto" }}>
          last {data.window_minutes} min
        </span>
      </div>

      {data.workers.length === 0 ? (
        <div style={{
          background: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "1.5rem",
          textAlign: "center",
          color: "#94a3b8",
          fontSize: "0.8rem",
        }}>
          No worker activity in the last {data.window_minutes} minutes.
        </div>
      ) : (
        <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
            <thead style={{ background: "#f8fafc" }}>
              <tr>
                <th style={thStyle}>Worker</th>
                <th style={thStyle}>Current Job</th>
                <th style={thStyle}>Jobs (10 min)</th>
                <th style={thStyle}>Avg Duration</th>
                <th style={thStyle}>Breakdown</th>
              </tr>
            </thead>
            <tbody>
              {data.workers.map((w) => (
                <WorkerRow key={w.worker_id} w={w} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
