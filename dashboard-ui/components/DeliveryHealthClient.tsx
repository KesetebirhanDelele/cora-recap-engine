"use client";

import { useEffect, useState } from "react";
import {
  fetchDeliveryDetail, fetchDeliveryHealth,
  type DeliveryChannel, type DeliveryDetailResponse, type DeliveryHealthResponse,
} from "@/lib/api";

const CARD: React.CSSProperties = {
  background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "0.875rem 1rem",
  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
};
const DOT = { green: "#16a34a", amber: "#d97706", red: "#dc2626", grey: "#94a3b8" } as const;

function ago(iso: string | null): string {
  if (!iso) return "never seen";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ago` : `${(h / 24).toFixed(1)} days ago`;
}

function Trend({ ch }: { ch: DeliveryChannel }) {
  const peak = Math.max(1, ...ch.trend.map((t) => t.sent));
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 44 }}>
      {ch.trend.map((t) => (
        <div key={t.day} title={`${t.day}: sent ${t.sent}, delivered ${t.delivered}`}
             style={{ position: "relative", width: 18, height: Math.max(3, (t.sent / peak) * 40), background: "#cbd5e1", borderRadius: 3 }}>
          <div style={{ position: "absolute", bottom: 0, width: "100%", height: `${t.sent ? (t.delivered / t.sent) * 100 : 0}%`,
                        background: DOT[ch.level === "red" ? "red" : "green"], borderRadius: 3 }} />
        </div>
      ))}
    </div>
  );
}

export default function DeliveryHealthClient() {
  const [d, setD] = useState<DeliveryHealthResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<DeliveryDetailResponse | null>(null);

  useEffect(() => {
    const load = () => fetchDeliveryHealth().then((x) => { setD(x); setErr(null); }).catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!open) { setDetail(null); return; }
    fetchDeliveryDetail(open).then(setDetail).catch(() => setDetail(null));
  }, [open]);

  if (err) return <div style={{ ...CARD, color: "#dc2626" }}>Could not load delivery health: {err}</div>;
  if (!d) return <div style={CARD}>Loading…</div>;

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div style={CARD}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "#64748b", fontSize: "0.8rem" }}>
                <th></th><th>Channel</th><th>Sent (24h)</th><th>Delivered</th>
                <th>Not confirmed (&gt;{d.confirm_minutes} min)</th><th>Failed</th><th>Last delivered</th><th>7 days</th>
              </tr>
            </thead>
            <tbody>
              {d.channels.map((c) => (
                <tr key={c.channel} onClick={() => setOpen(open === c.channel ? null : c.channel)}
                    style={{ borderTop: "1px solid #e2e8f0", cursor: "pointer", color: c.level === "grey" ? "#94a3b8" : undefined }}>
                  <td style={{ width: 18 }}>
                    <span title={c.reasons.join("; ")}
                          style={{ display: "inline-block", width: 12, height: 12, borderRadius: 6, background: DOT[c.level] }} />
                  </td>
                  <td style={{ fontWeight: 700 }}>{c.label}</td>
                  <td>{c.sent}</td>
                  <td>{c.delivered}{c.rate !== null ? ` (${Math.round(c.rate * 100)}%)` : ""}</td>
                  <td style={{ color: c.unconfirmed ? "#d97706" : undefined, fontWeight: c.unconfirmed ? 700 : 400 }}>{c.unconfirmed}</td>
                  <td style={{ color: c.failed ? "#dc2626" : undefined }}>{c.failed}</td>
                  <td>{ago(c.last_delivered_at)}</td>
                  <td><Trend ch={c} /></td>
                </tr>
              ))}
              <tr style={{ borderTop: "1px solid #e2e8f0" }}>
                <td><span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 6, background: DOT.grey }} /></td>
                <td style={{ fontWeight: 700 }}>Replies in</td>
                <td colSpan={2}>{d.replies.last_24h} in 24h (SMS {d.replies.by_channel.sms ?? 0} · email {d.replies.by_channel.email ?? 0} · calls {d.replies.by_channel.call ?? 0})</td>
                <td colSpan={2}></td>
                <td colSpan={2}>{ago(d.replies.last_at)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        {d.channels.filter((c) => c.reasons.length).map((c) => (
          <div key={c.channel} style={{ marginTop: 8, fontSize: "0.85rem", color: DOT[c.level] }}>
            <strong>{c.label}:</strong> {c.reasons.join("; ")}
          </div>
        ))}
        <div style={{ marginTop: 10, fontSize: "0.8rem", color: "#64748b" }}>
          Alert: a channel with no confirmed delivery for {Math.round(d.silence_hours / 24 * 10) / 10} days, or ≥20 sent with under 50% delivered,
          emails you (cc Ali). Calls count as delivered when they connect (person or voicemail); call logs arrive 10–15 min after the call.
          Delivery data last refreshed {ago(d.sync.last_run_at)}{d.sync.last_error ? ` — last error: ${d.sync.last_error}` : ""}
          {d.sync.backfill_done ? "" : " — still loading history"}.
        </div>
      </div>

      {open && (
        <div style={CARD}>
          <strong>{open} — failed and not-confirmed (last 24h)</strong>
          {!detail && <div style={{ color: "#94a3b8" }}>Loading…</div>}
          {detail && detail.items.length === 0 && <div style={{ color: "#16a34a" }}>Nothing failed or unconfirmed.</div>}
          {detail && detail.items.length > 0 && (
            <table style={{ width: "100%", fontSize: "0.82rem", marginTop: 6, borderCollapse: "collapse" }}>
              <thead><tr style={{ textAlign: "left", color: "#64748b" }}><th>Time</th><th>Phone</th><th>Email</th><th>GHL contact id</th><th>State</th><th>Reason</th></tr></thead>
              <tbody>
                {detail.items.map((i, k) => (
                  <tr key={k} style={{ borderTop: "1px solid #e2e8f0" }}>
                    <td>{new Date(i.at).toLocaleString()}</td><td style={{ userSelect: "all" }}>{i.phone || "—"}</td><td style={{ userSelect: "all" }}>{i.email || "—"}</td><td style={{ userSelect: "all", fontSize: "0.75rem", color: "#64748b" }}>{i.contact_id}</td>
                    <td style={{ color: i.state === "failed" ? "#dc2626" : "#d97706", fontWeight: 600 }}>{i.state}</td>
                    <td>{i.error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
