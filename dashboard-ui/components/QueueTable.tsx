"use client";

import type { MetricsResponse } from "@/types";

interface Props {
  queue: MetricsResponse["queue"];
}

export default function QueueTable({ queue }: Props) {
  return (
    <div>
      <h2 style={{ marginBottom: "1rem" }}>Stuck Jobs ({queue.stuck_jobs.length})</h2>
      {queue.stuck_jobs.length === 0 ? (
        <p style={{ color: "#475569" }}>No stuck jobs.</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
          <thead>
            <tr style={{ color: "#94a3b8", textAlign: "left", borderBottom: "1px solid #334155" }}>
              <th style={{ padding: "0.5rem" }}>Job Type</th>
              <th style={{ padding: "0.5rem" }}>Contact</th>
              <th style={{ padding: "0.5rem" }}>Run At</th>
              <th style={{ padding: "0.5rem" }}>Lag</th>
            </tr>
          </thead>
          <tbody>
            {queue.stuck_jobs.map((j) => (
              <tr key={j.job_id} style={{ borderBottom: "1px solid #1e293b" }}>
                <td style={{ padding: "0.5rem" }}>{j.job_type}</td>
                <td style={{ padding: "0.5rem" }}>
                  {j.contact_id ? (
                    <a href={`/lead/${j.contact_id}`} style={{ color: "#3b82f6" }}>
                      {j.contact_id}
                    </a>
                  ) : "—"}
                </td>
                <td style={{ padding: "0.5rem", color: "#94a3b8" }}>
                  {new Date(j.run_at).toLocaleString()}
                </td>
                <td style={{ padding: "0.5rem", color: "#f97316" }}>{j.lag_seconds}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2 style={{ marginTop: "2rem", marginBottom: "1rem" }}>
        Expired Leases ({queue.expired_leases.length})
      </h2>
      {queue.expired_leases.length === 0 ? (
        <p style={{ color: "#475569" }}>No expired leases.</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
          <thead>
            <tr style={{ color: "#94a3b8", textAlign: "left", borderBottom: "1px solid #334155" }}>
              <th style={{ padding: "0.5rem" }}>Job Type</th>
              <th style={{ padding: "0.5rem" }}>Worker</th>
              <th style={{ padding: "0.5rem" }}>Expired Ago</th>
            </tr>
          </thead>
          <tbody>
            {queue.expired_leases.map((l) => (
              <tr key={l.job_id} style={{ borderBottom: "1px solid #1e293b" }}>
                <td style={{ padding: "0.5rem" }}>{l.job_type}</td>
                <td style={{ padding: "0.5rem", color: "#94a3b8" }}>{l.worker_id}</td>
                <td style={{ padding: "0.5rem", color: "#ef4444" }}>{l.age_seconds}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
