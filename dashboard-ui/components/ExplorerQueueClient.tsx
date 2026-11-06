"use client";

import { useEffect, useState } from "react";
import { fetchExplorerQueue } from "@/lib/api";
import type { ExplorerQueueResponse, ExplorerQueueRun } from "@/types";

const STATUS_COLOR: Record<string, string> = {
  completed: "#16a34a",
  running: "#2563eb",
  skipped: "#94a3b8",
  failed: "#dc2626",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "1rem 1.25rem", flex: 1, boxShadow: "0 1px 3px rgba(0,0,0,0.04)" }}>
      <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.02em" }}>{value}</div>
      <div style={{ fontSize: "0.78rem", color: "#64748b", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function RunRow({ run }: { run: ExplorerQueueRun }) {
  const color = STATUS_COLOR[run.status] ?? "#64748b";
  const skipped = run.skipped_no_ghl_url + run.skipped_ai_campaign + run.skipped_no_access;
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderLeft: `3px solid ${color}`, borderRadius: 8, padding: "0.75rem 1rem", display: "flex", alignItems: "center", gap: "0.875rem", fontSize: "0.82rem" }}>
      <span style={{ fontSize: "0.62rem", fontWeight: 700, textTransform: "uppercase" as const, letterSpacing: "0.07em", color, background: `${color}12`, border: `1px solid ${color}30`, borderRadius: 4, padding: "1px 6px", flexShrink: 0 }}>
        {run.status}{run.dry_run ? " · dry-run" : ""}
      </span>
      <span style={{ color: "#374151" }}>
        {run.rows_fetched} fetched · {run.contacts_tagged} tagged · {run.contacts_already_tagged} already tagged ·{" "}
        {run.contacts_untagged} left the list · {skipped} skipped
        {run.contacts_failed > 0 && ` · ${run.contacts_failed} failed`}
      </span>
      {run.error_message && <span style={{ color: run.status === "failed" ? "#dc2626" : "#64748b", fontSize: "0.75rem" }}>— {run.error_message}</span>}
      <span style={{ marginLeft: "auto", fontSize: "0.72rem", color: "#94a3b8", whiteSpace: "nowrap" as const }}>{timeAgo(run.started_at)}</span>
    </div>
  );
}

export default function ExplorerQueueClient() {
  const [data, setData] = useState<ExplorerQueueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchExplorerQueue(10)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  if (error) return <p style={{ color: "#dc2626", fontSize: "0.875rem" }}>Error: {error}</p>;
  if (!data) return <p style={{ color: "#64748b", fontSize: "0.875rem" }}>Loading…</p>;

  const lastRun = data.runs[0];
  const stale = !lastRun || Date.now() - new Date(lastRun.started_at).getTime() > 36 * 3600 * 1000;
  const sw = data.job_enabled
    ? data.ghl_writes_enabled ? "ON — writing to GHL" : "ON — GHL still in shadow mode (no writes)"
    : "OFF";

  return (
    <div>
      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "0.75rem" }}>
        <StatTile label="On Rose's list now" value={String(data.queue.in_queue)} />
        <StatTile label="Never called yet" value={String(data.queue.never_called)} />
        <StatTile label="Called at least once, not reached" value={String(data.queue.called_not_reached)} />
        <StatTile label="Callback pending (kept on list)" value={String(data.queue.callback_pending)} />
        <StatTile label="Left the list (see reasons below)" value={String(data.queue.exited)} />
      </div>
      <p style={{ fontSize: "0.8rem", color: stale && data.job_enabled ? "#dc2626" : "#64748b", margin: "0 0 1.25rem" }}>
        Job switch: <strong>{sw}</strong> · Last run: {lastRun ? timeAgo(lastRun.started_at) : "never"}
        {stale && data.job_enabled && " — no run in the last 36 hours, check the worker"}
      </p>

      {data.queue.exited > 0 && (
        <p style={{ fontSize: "0.8rem", color: "#475569", margin: "-0.75rem 0 1.25rem" }}>
          Left the list because:{" "}
          {Object.entries(data.exits).map(([k, n]) => `${k.replace(/_/g, " ")} ${n}`).join(" · ")}
        </p>
      )}

      <h2 style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b", margin: "0 0 0.75rem" }}>Called at least once, still on the list</h2>
      {data.attempted.length === 0 ? (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "1.25rem", textAlign: "center" as const, color: "#94a3b8", fontSize: "0.85rem", marginBottom: "1.25rem" }}>
          No contact on the list has been called since it was tagged.
        </div>
      ) : (
        <table style={{ width: "100%", fontSize: "0.82rem", background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, borderCollapse: "collapse", marginBottom: "1.25rem" }}>
          <thead>
            <tr style={{ textAlign: "left" as const, color: "#64748b" }}>
              <th style={{ padding: "0.5rem 0.75rem" }}>Contact (opens GHL)</th><th>Score</th><th>Stage</th><th>Calls</th><th>Last attempt</th>
            </tr>
          </thead>
          <tbody>
            {data.attempted.map((a) => (
              <tr key={a.ghl_contact_id} style={{ borderTop: "1px solid #f1f5f9" }}>
                <td style={{ padding: "0.5rem 0.75rem" }}>
                  <a href={`https://app.gohighlevel.com/v2/location/${a.ghl_location_id}/contacts/detail/${a.ghl_contact_id}`} target="_blank" rel="noreferrer" style={{ color: "#2563eb" }}>
                    {a.ghl_contact_id}
                  </a>
                </td>
                <td>{a.score ?? "—"}</td>
                <td>{a.stage_tag ? a.stage_tag.replace("ent-stage-", "") : "—"}</td>
                <td>{a.attempts}</td>
                <td>{timeAgo(a.last_attempt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {lastRun?.feed_capped && (
        <p style={{ fontSize: "0.8rem", color: "#b45309", margin: "0 0 1.25rem" }}>
          The last pull returned the maximum of {lastRun.rows_fetched} rows, so more leads exist than we can see until paging is added.
        </p>
      )}

      <h2 style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b", margin: "0 0 0.75rem" }}>Leads we could not tag (report to Ali)</h2>
      {Object.values(data.skipped ?? {}).every((l) => l.length === 0) ? (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "1.25rem", textAlign: "center" as const, color: "#94a3b8", fontSize: "0.85rem", marginBottom: "1.25rem" }}>
          Nothing skipped in the last run.
        </div>
      ) : (
        <table style={{ width: "100%", fontSize: "0.82rem", background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, borderCollapse: "collapse", marginBottom: "1.25rem" }}>
          <thead>
            <tr style={{ textAlign: "left" as const, color: "#64748b" }}>
              <th style={{ padding: "0.5rem 0.75rem" }}>Why</th><th>Rank</th><th>Score</th><th>State</th><th>Enrollment id</th><th>Lead id</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.skipped).flatMap(([reason, list]) =>
              list.map((s, i) => (
                <tr key={`${reason}-${i}`} style={{ borderTop: "1px solid #f1f5f9" }}>
                  <td style={{ padding: "0.5rem 0.75rem" }}>{reason === "no_ghl_contact" ? "No GHL contact" : "Wrong sub-account"}</td>
                  <td>{s.rank ?? "—"}</td>
                  <td>{s.score ?? "—"}</td>
                  <td>{s.primary_state ?? "—"}</td>
                  <td>{s.enrollment_id ?? "—"}</td>
                  <td>{s.lead_id ?? "—"}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}

      <h2 style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b", margin: "0 0 0.75rem" }}>Recent runs</h2>
      {data.runs.length === 0 ? (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "2rem", textAlign: "center" as const, color: "#94a3b8", fontSize: "0.875rem" }}>
          No runs yet — the nightly job hasn&apos;t executed.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {data.runs.map((r) => <RunRow key={r.id} run={r} />)}
        </div>
      )}
    </div>
  );
}
