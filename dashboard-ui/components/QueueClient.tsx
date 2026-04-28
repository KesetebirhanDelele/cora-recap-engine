"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchMetrics, fetchWorkerActivity, cancelLeadJobs } from "@/lib/api";
import type { MetricsResponse, WorkerActivityResponse } from "@/types";
import QueueTable from "@/components/QueueTable";
import WorkerActivityTable from "@/components/WorkerActivityTable";

const REFRESH_MS = 30_000;

export default function QueueClient() {
  const [queue, setQueue] = useState<MetricsResponse["queue"] | null>(null);
  const [activity, setActivity] = useState<WorkerActivityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([fetchMetrics(), fetchWorkerActivity()])
      .then(([m, a]) => {
        setQueue(m.queue);
        setActivity(a);
        setLastRefreshed(new Date());
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  async function handleCancelJob(contactId: string) {
    await cancelLeadJobs({ contact_id: contactId, reason: "operator_cancelled_from_queue_health" });
    load();
  }

  if (loading && !queue) return <p style={{ color: "#64748b", fontSize: "0.875rem" }}>Loading…</p>;
  if (error) return <p style={{ color: "#dc2626", fontSize: "0.875rem" }}>Error: {error}</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2.5rem" }}>
      {activity && <WorkerActivityTable data={activity} />}

      {queue && (
        <QueueTable queue={queue} onCancelJob={handleCancelJob} />
      )}

      {lastRefreshed && (
        <p style={{ fontSize: "0.65rem", color: "#94a3b8", textAlign: "right", marginTop: "-1.5rem" }}>
          Auto-refreshes every 30s · Last: {lastRefreshed.toLocaleTimeString()}
        </p>
      )}
    </div>
  );
}
