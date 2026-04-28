"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchMetrics, fetchWorkerActivity, fetchWorkerActivityTrend, fetchWebhookFailures, cancelLeadJobs } from "@/lib/api";
import type { MetricsResponse, WorkerActivityResponse, WorkerTrendResponse, WebhookFailuresResponse } from "@/types";
import QueueTable from "@/components/QueueTable";
import WorkerActivityTable from "@/components/WorkerActivityTable";
import WorkerTrendChart from "@/components/WorkerTrendChart";
import WebhookFailurePanel from "@/components/WebhookFailurePanel";

const REFRESH_MS = 30_000;

export default function QueueClient() {
  const [queue, setQueue]           = useState<MetricsResponse["queue"] | null>(null);
  const [activity, setActivity]     = useState<WorkerActivityResponse | null>(null);
  const [trend, setTrend]           = useState<WorkerTrendResponse | null>(null);
  const [webhooks, setWebhooks]     = useState<WebhookFailuresResponse | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([fetchMetrics(), fetchWorkerActivity(), fetchWorkerActivityTrend(), fetchWebhookFailures()])
      .then(([m, a, t, wf]) => {
        setQueue(m.queue);
        setActivity(a);
        setTrend(t);
        setWebhooks(wf);
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

      {/* 1 — 60-minute trend charts */}
      {trend && (
        <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "1.25rem 1.5rem", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          <WorkerTrendChart data={trend} />
        </div>
      )}

      {/* 2 — Webhook delivery failures */}
      {webhooks && <WebhookFailurePanel data={webhooks} />}

      {/* 3 — Stuck jobs + expired leases */}
      {queue && (
        <QueueTable queue={queue} onCancelJob={handleCancelJob} />
      )}

      {/* 4 — Per-worker 10-min activity table */}
      {activity && <WorkerActivityTable data={activity} />}

      {lastRefreshed && (
        <p style={{ fontSize: "0.65rem", color: "#94a3b8", textAlign: "right", marginTop: "-1.5rem" }}>
          Auto-refreshes every 30s · Last: {lastRefreshed.toLocaleTimeString()}
        </p>
      )}
    </div>
  );
}
