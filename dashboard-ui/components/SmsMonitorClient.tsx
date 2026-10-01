"use client";

import { useEffect, useState } from "react";
import { fetchSmsMonitor, type SmsMonitorResponse } from "@/lib/api";

const CARD: React.CSSProperties = {
  background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "0.875rem 1rem",
  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
};
const LEVEL_COLOR = { ok: "#16a34a", warning: "#d97706", exhausted: "#dc2626" } as const;
const STATUS_COLOR: Record<string, string> = {
  sent: "#16a34a", reserved: "#2563eb", blocked: "#dc2626", deferred: "#d97706", failed: "#64748b",
};

export default function SmsMonitorClient() {
  const [d, setD] = useState<SmsMonitorResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () => fetchSmsMonitor().then((x) => { setD(x); setErr(null); }).catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  if (err) return <div style={{ ...CARD, color: "#dc2626" }}>Could not load SMS monitor: {err}</div>;
  if (!d) return <div style={CARD}>Loading…</div>;

  const pct = Math.min(100, Math.round((d.segments_today / Math.max(d.daily_cap, 1)) * 100));
  const hours = Object.entries(d.by_hour_pacific);
  const peak = Math.max(1, ...hours.map(([, v]) => v));

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div style={CARD}>
        <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <strong>Today&apos;s budget (US Pacific day)</strong>
          <span style={{ color: LEVEL_COLOR[d.level], fontWeight: 700 }}>
            {d.segments_today} / {d.daily_cap} segments · {d.remaining} left
          </span>
        </div>
        <div style={{ height: 12, background: "#e2e8f0", borderRadius: 6, margin: "0.6rem 0" }}>
          <div style={{ width: `${pct}%`, height: 12, borderRadius: 6, background: LEVEL_COLOR[d.level] }} />
        </div>
        <div style={{ fontSize: "0.85rem", color: "#475569" }}>
          Resets {new Date(d.day_resets_at).toLocaleString()} · hard maximum {d.hard_max} (Twilio sole-proprietor limit is 1,000/day
          to T-Mobile) · when full, texts move to the next day at a legal hour for the lead.
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "0.75rem" }}>
        {([
          ["Texts sent today", d.messages_today], ["Blocked by gate", d.blocked_today],
          ["Moved to next day", d.deferred_today], ["Failed writes", d.failed_today],
        ] as const).map(([k, v]) => (
          <div key={k} style={CARD}>
            <div style={{ fontSize: "0.8rem", color: "#64748b" }}>{k}</div>
            <div style={{ fontSize: "1.6rem", fontWeight: 800 }}>{v}</div>
          </div>
        ))}
      </div>

      <div style={CARD}>
        <strong>Segments per hour (Pacific)</strong>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 70, marginTop: 8 }}>
          {hours.length === 0 && <span style={{ color: "#94a3b8" }}>No texts yet today.</span>}
          {hours.map(([h, v]) => (
            <div key={h} title={`${h} — ${v}`} style={{ textAlign: "center", fontSize: 10 }}>
              <div style={{ width: 22, height: Math.max(3, (v / peak) * 50), background: "#2563eb", borderRadius: 3 }} />
              {h.slice(0, 2)}
            </div>
          ))}
        </div>
        <div style={{ fontSize: "0.8rem", color: "#64748b", marginTop: 6 }}>
          Pacing: ≥{d.limits.min_gap_seconds}s between texts, ≤{d.limits.per_minute_cap}/minute, ≤{d.limits.max_segments_per_message} segments each.
        </div>
      </div>

      <div style={CARD}>
        <strong>Latest 25 texts</strong>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: "0.82rem", borderCollapse: "collapse", marginTop: 6 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "#64748b" }}>
                <th>Time</th><th>Source</th><th>Status</th><th>Seg</th><th>Contact</th><th>Text / reason</th>
              </tr>
            </thead>
            <tbody>
              {d.recent.map((r, i) => (
                <tr key={i} style={{ borderTop: "1px solid #e2e8f0" }}>
                  <td>{new Date(r.at).toLocaleTimeString()}</td>
                  <td>{r.source}</td>
                  <td style={{ color: STATUS_COLOR[r.status] ?? "#334155", fontWeight: 600 }}>{r.status}</td>
                  <td>{r.segments}</td>
                  <td>{r.contact_id.slice(0, 8)}…</td>
                  <td>{r.reason ? `${r.reason} — ` : ""}{r.preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
