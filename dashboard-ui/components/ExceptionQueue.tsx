"use client";

import type { MetricsResponse } from "@/types";

interface Props {
  queue: MetricsResponse["queue"];
}

/**
 * ExceptionQueue — stub component for the exceptions view.
 *
 * The full exception list is fetched from GET /v1/exceptions (existing endpoint
 * on port 8000). This component renders the stuck job list from metrics as a
 * proxy until the exceptions endpoint is wired into the v2 dashboard API.
 */
export default function ExceptionQueue({ queue }: Props) {
  const total = queue.stuck_jobs.length + queue.expired_leases.length;

  if (total === 0) {
    return <p style={{ color: "#475569" }}>No exceptions or stuck jobs.</p>;
  }

  return (
    <div>
      <p style={{ color: "#94a3b8", fontSize: "0.875rem" }}>
        {queue.stuck_jobs.length} stuck job(s), {queue.expired_leases.length} expired lease(s)
      </p>
      <p style={{ color: "#64748b", fontSize: "0.8rem" }}>
        See <a href="/queue" style={{ color: "#3b82f6" }}>Queue Health</a> for details.
      </p>
    </div>
  );
}
