"use client";

import { useEffect, useState } from "react";
import { fetchCardMetrics, fetchTagAiColdLeadsRuns } from "@/lib/api";
import type { CardMetricsResponse, TagAiColdLeadsRun } from "@/types";

const STATUS_COLOR: Record<string, string> = {
  completed: "#16a34a",
  running:   "#2563eb",
  skipped:   "#94a3b8",
  failed:    "#dc2626",
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "1rem 1.25rem",
        flex: 1,
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
      }}
    >
      <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.02em" }}>
        {value}
      </div>
      <div style={{ fontSize: "0.78rem", color: "#64748b", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function RunRow({ run }: { run: TagAiColdLeadsRun }) {
  const color = STATUS_COLOR[run.status] ?? "#64748b";
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderLeft: `3px solid ${color}`,
        borderRadius: 8,
        padding: "0.75rem 1rem",
        display: "flex",
        alignItems: "center",
        gap: "0.875rem",
        fontSize: "0.82rem",
      }}
    >
      <span
        style={{
          fontSize: "0.62rem", fontWeight: 700, textTransform: "uppercase" as const,
          letterSpacing: "0.07em", color,
          background: `${color}12`, border: `1px solid ${color}30`,
          borderRadius: 4, padding: "1px 6px", flexShrink: 0,
        }}
      >
        {run.status}{run.dry_run ? " · dry-run" : ""}
      </span>
      <span style={{ color: "#374151" }}>
        {run.contacts_tagged} tagged
        {run.contacts_skipped_already_tagged > 0 && `, ${run.contacts_skipped_already_tagged} already tagged`}
        {run.contacts_failed > 0 && `, ${run.contacts_failed} failed`}
        {" "}of {run.contacts_scanned} scanned
      </span>
      {run.error_message && (
        <span style={{ color: "#dc2626", fontSize: "0.75rem" }}>— {run.error_message}</span>
      )}
      <span style={{ marginLeft: "auto", fontSize: "0.72rem", color: "#94a3b8", whiteSpace: "nowrap" as const }}>
        {timeAgo(run.started_at)}
      </span>
    </div>
  );
}

export default function TagAiColdLeadsClient() {
  const [cardMetrics, setCardMetrics] = useState<CardMetricsResponse | null>(null);
  const [runs, setRuns] = useState<TagAiColdLeadsRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchCardMetrics(), fetchTagAiColdLeadsRuns(10)])
      .then(([cm, runsRes]) => {
        if (cancelled) return;
        setCardMetrics(cm);
        setRuns(runsRes.runs);
      })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <p style={{ color: "#64748b", fontSize: "0.875rem" }}>Loading…</p>;
  if (error) return <p style={{ color: "#dc2626", fontSize: "0.875rem" }}>Error: {error}</p>;

  const backlog = cardMetrics?.ai_cold_lead_tagging_backlog.value ?? "—";
  const tagged24h = cardMetrics?.ai_cold_lead_tagging_tagged_24h.value ?? "—";

  return (
    <div>
      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1.25rem" }}>
        <StatTile label="Currently matching filter, not yet tagged" value={String(backlog)} />
        <StatTile label="Tagged in last 24 hours" value={String(tagged24h)} />
      </div>

      <h2 style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b", margin: "0 0 0.75rem" }}>
        Recent runs
      </h2>

      {runs.length === 0 ? (
        <div
          style={{
            background: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            padding: "2rem",
            textAlign: "center" as const,
            color: "#94a3b8",
            fontSize: "0.875rem",
          }}
        >
          No runs yet — the daily scan hasn&apos;t executed, or is still disabled
          (AI_COLD_LEAD_TAGGING_ENABLED).
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {runs.map((r) => <RunRow key={r.id} run={r} />)}
        </div>
      )}
    </div>
  );
}
