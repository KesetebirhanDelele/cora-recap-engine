"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchMetrics, cancelLeadJobs } from "@/lib/api";
import type { MetricsResponse } from "@/types";
import QueueTable from "@/components/QueueTable";

export default function QueueClient() {
  const [queue, setQueue] = useState<MetricsResponse["queue"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchMetrics()
      .then((m) => setQueue(m.queue))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleCancelJob(contactId: string) {
    await cancelLeadJobs({ contact_id: contactId, reason: "operator_cancelled_from_queue_health" });
    // Re-fetch so cancelled jobs disappear from the stuck list
    load();
  }

  if (loading) return <p style={{ color: "#64748b", fontSize: "0.875rem" }}>Loading…</p>;
  if (error)   return <p style={{ color: "#dc2626", fontSize: "0.875rem" }}>Error: {error}</p>;
  if (!queue)  return null;

  return <QueueTable queue={queue} onCancelJob={handleCancelJob} />;
}
