"use client";

import { useState } from "react";
import type { MetricsResponse } from "@/types";

interface Props {
  queue: MetricsResponse["queue"];
  onCancelJob?: (contactId: string) => Promise<void>;
}

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.625rem", marginBottom: "0.875rem" }}>
      <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#0f172a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </span>
      <span style={{
        background: count > 0 ? "#fef2f2" : "#f1f5f9",
        color: count > 0 ? "#dc2626" : "#94a3b8",
        border: `1px solid ${count > 0 ? "#fecaca" : "#e2e8f0"}`,
        borderRadius: 99,
        padding: "0 0.5rem",
        fontSize: "0.7rem",
        fontWeight: 700,
        lineHeight: "1.5rem",
      }}>
        {count}
      </span>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div style={{
      background: "#ffffff",
      border: "1px solid #e2e8f0",
      borderRadius: 8,
      padding: "1.5rem",
      textAlign: "center",
      color: "#94a3b8",
      fontSize: "0.8rem",
    }}>
      {message}
    </div>
  );
}

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
};

export default function QueueTable({ queue, onCancelJob }: Props) {
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());
  const [cancelErrors, setCancelErrors] = useState<Record<string, string>>({});
  const [cancelled, setCancelled] = useState<Set<string>>(new Set());

  async function handleCancel(contactId: string) {
    if (!onCancelJob) return;
    setCancelling((prev) => new Set(prev).add(contactId));
    setCancelErrors((prev) => { const n = { ...prev }; delete n[contactId]; return n; });
    try {
      await onCancelJob(contactId);
      setCancelled((prev) => new Set(prev).add(contactId));
    } catch (e) {
      setCancelErrors((prev) => ({ ...prev, [contactId]: String(e) }));
    } finally {
      setCancelling((prev) => { const n = new Set(prev); n.delete(contactId); return n; });
    }
  }

  const showActions = Boolean(onCancelJob);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <div>
        <SectionHeader label="Stuck Jobs" count={queue.stuck_jobs.length} />
        {queue.stuck_jobs.length === 0 ? (
          <EmptyState message="No stuck jobs — queue is healthy." />
        ) : (
          <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
              <thead style={{ background: "#f8fafc" }}>
                <tr>
                  <th style={thStyle}>Job Type</th>
                  <th style={thStyle}>Contact</th>
                  <th style={thStyle}>Run At</th>
                  <th style={thStyle}>Lag</th>
                  {showActions && <th style={thStyle}>Action</th>}
                </tr>
              </thead>
              <tbody>
                {queue.stuck_jobs.map((j) => {
                  const isCancelling = j.contact_id ? cancelling.has(j.contact_id) : false;
                  const isCancelled  = j.contact_id ? cancelled.has(j.contact_id) : false;
                  const cancelError  = j.contact_id ? cancelErrors[j.contact_id] : undefined;
                  return (
                    <tr key={j.job_id}>
                      <td style={tdStyle}>{j.job_type}</td>
                      <td style={tdStyle}>
                        {j.contact_id ? (
                          <a href={`/lead/${j.contact_id}`} style={{ color: "#2563eb", textDecoration: "none", fontWeight: 500 }}>
                            {j.contact_id}
                          </a>
                        ) : "—"}
                      </td>
                      <td style={{ ...tdStyle, color: "#64748b" }}>
                        {new Date(j.run_at).toLocaleString()}
                      </td>
                      <td style={{ ...tdStyle, color: "#ea580c", fontWeight: 700 }}>{j.lag_seconds}s</td>
                      {showActions && (
                        <td style={tdStyle}>
                          {j.contact_id ? (
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              {isCancelled ? (
                                <span style={{ fontSize: "0.7rem", color: "#16a34a", fontWeight: 600 }}>Cancelled</span>
                              ) : (
                                <button
                                  onClick={() => handleCancel(j.contact_id!)}
                                  disabled={isCancelling}
                                  style={{
                                    padding: "0.2rem 0.55rem",
                                    border: "1px solid #fecaca",
                                    borderRadius: 5,
                                    background: "#fef2f2",
                                    color: "#dc2626",
                                    fontSize: "0.7rem",
                                    fontWeight: 600,
                                    cursor: isCancelling ? "not-allowed" : "pointer",
                                    opacity: isCancelling ? 0.6 : 1,
                                    whiteSpace: "nowrap" as const,
                                  }}
                                >
                                  {isCancelling ? "…" : "Cancel jobs"}
                                </button>
                              )}
                              {cancelError && (
                                <span style={{ fontSize: "0.65rem", color: "#dc2626" }}>{cancelError}</span>
                              )}
                            </div>
                          ) : (
                            <span style={{ fontSize: "0.7rem", color: "#94a3b8" }}>—</span>
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <SectionHeader label="Expired Leases" count={queue.expired_leases.length} />
        {queue.expired_leases.length === 0 ? (
          <EmptyState message="No expired leases." />
        ) : (
          <>
            <p style={{ fontSize: "0.75rem", color: "#64748b", marginBottom: "0.625rem" }}>
              Expired leases are auto-recovered by the worker. No manual action required.
            </p>
            <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                <thead style={{ background: "#f8fafc" }}>
                  <tr>
                    <th style={thStyle}>Job Type</th>
                    <th style={thStyle}>Worker</th>
                    <th style={thStyle}>Expired Ago</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.expired_leases.map((l) => (
                    <tr key={l.job_id}>
                      <td style={tdStyle}>{l.job_type}</td>
                      <td style={{ ...tdStyle, color: "#64748b" }}>{l.worker_id}</td>
                      <td style={{ ...tdStyle, color: "#dc2626", fontWeight: 700 }}>{l.age_seconds}s</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
