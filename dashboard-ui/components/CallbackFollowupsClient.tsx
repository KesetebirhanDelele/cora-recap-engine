"use client";

import { useCallback, useEffect, useState } from "react";
import { decideCallbackFollowup, fetchCallbackFollowups } from "@/lib/api";
import type { CallbackFollowupLead, CallbackFollowupResponse } from "@/types";

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const REASON: Record<string, string> = {
  no_time: "no time given",
  time_unclear: "time unclear",
  time_not_usable: "time not usable",
  slot_taken: "slot taken",
  no_calendar: "no calendar",
  calendar_unreadable: "calendar unreadable",
  booking_failed: "booking failed",
  clear_time_free_slot: "clear time, slot free",
};

function StatTile({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "1rem 1.25rem", flex: 1, minWidth: 150, boxShadow: "0 1px 3px rgba(0,0,0,0.04)" }}>
      <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#0f172a", letterSpacing: "-0.02em" }}>{value}</div>
      <div style={{ fontSize: "0.78rem", color: "#64748b", marginTop: 2 }}>{label}</div>
      {hint && <div style={{ fontSize: "0.68rem", color: "#94a3b8", marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

function StatusBadge({ lead }: { lead: CallbackFollowupLead }) {
  let text = lead.status;
  let color = "#64748b";
  if (lead.approval_status === "pending") { text = "waiting for approval"; color = "#b45309"; }
  else if (lead.approval_status === "rejected") { text = "rejected"; color = "#94a3b8"; }
  else if (lead.status === "done") { text = lead.action === "book" ? "booked + staff emailed" : "staff emailed"; color = "#16a34a"; }
  else if (lead.status === "failed") { text = "failed"; color = "#dc2626"; }
  return (
    <span style={{ fontSize: "0.68rem", fontWeight: 700, color, background: `${color}14`, border: `1px solid ${color}30`, borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap" as const }}>
      {text}
    </span>
  );
}

export default function CallbackFollowupsClient() {
  const [data, setData] = useState<CallbackFollowupResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchCallbackFollowups(100).then(setData).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function decide(lead: CallbackFollowupLead, decision: "approve" | "reject") {
    const what = decision === "approve"
      ? (lead.action === "book"
          ? `Approve: Cora will book a call-back on ${lead.staff_name}'s calendar for ${lead.requested_time?.replace("T", " ")} and email ${lead.staff_name}.`
          : `Approve: Cora will email ${lead.staff_name} to call ${lead.lead_name || "this lead"} back.`)
      : "Reject: this is not a genuine request. Nothing will be sent.";
    if (!window.confirm(what)) return;
    setBusy(lead.id);
    try { await decideCallbackFollowup(lead.id, decision); load(); }
    catch (e) { window.alert(`Could not ${decision}: ${String(e)}`); }
    finally { setBusy(null); }
  }

  if (error) return <p style={{ color: "#dc2626", fontSize: "0.875rem" }}>Error: {error}</p>;
  if (!data) return <p style={{ color: "#64748b", fontSize: "0.875rem" }}>Loading…</p>;

  const c = data.counts;
  const shadow = !data.calls_enabled || !data.emails_enabled;
  return (
    <div>
      <p style={{ fontSize: "0.8rem", color: shadow ? "#b45309" : "#16a34a", margin: "0 0 0.75rem" }}>
        Calls: <strong>{data.calls_enabled ? "ON (acts automatically)" : "shadow mode (waits for your approval)"}</strong> ·
        Unanswered emails: <strong>{data.emails_enabled ? "ON (acts automatically)" : "shadow mode (waits for your approval)"}</strong>
      </p>

      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "0.75rem", flexWrap: "wrap" as const }}>
        <StatTile label="Leads who triggered a call-back / booking request" value={c.requests} hint={`${c.requests_7d} in the last 7 days · ${c.from_calls} calls, ${c.from_emails} emails`} />
        <StatTile label="Booking planned" value={c.booking_planned} hint="clear time, slot free" />
        <StatTile label="Email to staff planned" value={c.email_planned} hint="no / unclear time" />
        <StatTile label="Booking or email sent to staff" value={c.staff_notified} hint={`${c.booked} bookings · ${c.failed} failed`} />
        <StatTile label="Waiting for your approval" value={c.pending_approval} hint={`${c.approved} approved · ${c.rejected} rejected`} />
        <StatTile label="Lead reached by staff" value={c.contacted} hint="a connected staff call after the request" />
      </div>

      <h2 style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b", margin: "1rem 0 0.75rem" }}>Leads and students who need a call back</h2>
      {data.leads.length === 0 ? (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "1.25rem", textAlign: "center" as const, color: "#94a3b8", fontSize: "0.85rem" }}>
          No call-back requests recorded yet.
        </div>
      ) : (
        <div style={{ overflowX: "auto" as const }}>
          <table style={{ width: "100%", fontSize: "0.82rem", background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" as const, color: "#64748b" }}>
                <th style={{ padding: "0.5rem 0.75rem" }}>Lead / student</th><th>Email</th><th>Phone</th><th>Staff needed</th>
                <th>What it is about</th><th>Plan</th><th>Status</th><th>Reached</th><th></th>
              </tr>
            </thead>
            <tbody>
              {data.leads.map((l) => (
                <tr key={l.id} style={{ borderTop: "1px solid #f1f5f9", verticalAlign: "top" as const }}>
                  <td style={{ padding: "0.5rem 0.75rem" }}>
                    <strong>{l.lead_name || "(no name)"}</strong>
                    <div style={{ fontSize: "0.7rem", color: "#94a3b8" }}>{l.source === "call" ? "failed transfer" : "unanswered email"} · {timeAgo(l.created_at)}</div>
                  </td>
                  <td>{l.lead_email || "—"}</td>
                  <td style={{ whiteSpace: "nowrap" as const }}>{l.lead_phone || "—"}</td>
                  <td>{l.staff_name || "—"}</td>
                  <td style={{ maxWidth: 320 }}>{l.summary || "—"}</td>
                  <td style={{ fontSize: "0.75rem" }}>
                    {l.action === "book" ? `Book ${l.requested_time?.replace("T", " ") ?? ""}` : "Email staff"}
                    <div style={{ color: "#94a3b8" }}>{REASON[l.reason] ?? l.reason}</div>
                  </td>
                  <td><StatusBadge lead={l} /></td>
                  <td style={{ fontSize: "0.75rem", color: l.contacted_at ? "#16a34a" : "#94a3b8" }}>{l.contacted_at ? timeAgo(l.contacted_at) : "not yet"}</td>
                  <td style={{ whiteSpace: "nowrap" as const }}>
                    {l.approval_status === "pending" && (
                      <>
                        <button disabled={busy === l.id} onClick={() => decide(l, "approve")}
                          style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 6, padding: "4px 10px", fontSize: "0.75rem", fontWeight: 600, cursor: "pointer", marginRight: 6 }}>Approve</button>
                        <button disabled={busy === l.id} onClick={() => decide(l, "reject")}
                          style={{ background: "#fff", color: "#64748b", border: "1px solid #cbd5e1", borderRadius: 6, padding: "4px 10px", fontSize: "0.75rem", cursor: "pointer" }}>Not genuine</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
