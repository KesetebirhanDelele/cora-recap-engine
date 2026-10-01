"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchWrongDates,
  sendDateCorrection,
  sendDateCorrectionAll,
  sendTestCorrection,
  dismissWrongDate,
  dismissWrongDatesBulk,
  type BulkSendResult,
  type CorrectionChannel,
  type WrongDatesResponse,
  type WrongDateIncident,
} from "@/lib/api";

const CARD: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  padding: "0.875rem 1rem",
  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
};

const PRIMARY_BTN: React.CSSProperties = {
  background: "#2563eb", color: "#fff", border: 0, borderRadius: 6,
  padding: "0.45rem 0.8rem", fontWeight: 600,
};
const SECONDARY_BTN: React.CSSProperties = {
  background: "#fff", color: "#475569", border: "1px solid #cbd5e1", borderRadius: 6, padding: "0.45rem 0.8rem",
};

const CHANNEL_LABEL: Record<CorrectionChannel, string> = { email: "email", sms: "SMS" };

function bulkSummary(
  channel: CorrectionChannel, sent: number, failed: number, blocked: number, last: BulkSendResult | null,
): string {
  if (last?.shadow) return "Shadow mode: nothing was sent (GHL writes are off). Incidents left open.";
  const parts = [`Done: ${sent} ${CHANNEL_LABEL[channel]} correction(s) sent`];
  if (failed) parts.push(`${failed} failed (left open, will retry)`);
  if (blocked) parts.push(`${blocked} skipped — DND / unsubscribed / do-not-contact / no address (closed)`);
  if (last?.held) {
    const when = last.next_window_opens ? ` — next window opens ${new Date(last.next_window_opens).toLocaleString()}` : "";
    parts.push(`${last.held} held (outside the sending window or the lead replied; still open)${when}`);
  }
  let msg = parts.join(", ") + ".";
  if (last?.daily_cap_reached) msg += ` Daily cap of ${last.daily_cap} reached — the rest can go out in 24h.`;
  if (last?.stopped_early) msg += " Stopped early after repeated failures: " + (last.errors ?? []).join("; ");
  return msg;
}

const KIND_LABEL = { class: "Class start", open_house: "Open house" } as const;

type Tab = "open" | "corrected" | "dismissed";

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
}

function IncidentRow({
  inc,
  previews,
  smsEnabled,
  onChanged,
}: {
  inc: WrongDateIncident;
  previews: { email: string; sms: string };
  smsEnabled: boolean;
  onChanged: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const open = inc.status === "open";

  async function send(channel: CorrectionChannel) {
    if (!window.confirm(`Send this correction ${CHANNEL_LABEL[channel]} to ${inc.contact_id}?\n\n${previews[channel]}`)) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await sendDateCorrection(inc.id, channel);
      onChanged(
        r.status === "skipped"
          ? `Not sent: ${r.reason}. Incident closed.`
          : r.status === "held"
          ? `Not sent now: ${r.reason}${r.next_open ? ` (next window opens ${new Date(r.next_open).toLocaleString()})` : ""}. Incident left open.`
          : r.status === "shadow"
          ? "Shadow mode: nothing was sent (GHL writes are off). Incident left open."
          : `Correction ${CHANNEL_LABEL[channel]} sent to ${inc.contact_id}.` +
            (r.also_closed ? ` Also closed ${r.also_closed} other open incident(s) for this lead.` : "")
      );
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    setBusy(true);
    setErr(null);
    try {
      await dismissWrongDate(inc.id);
      onChanged("Incident dismissed.");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ ...CARD, borderLeft: `3px solid ${open ? "#d97706" : "#16a34a"}` }}>
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", marginBottom: 4 }}>
            <Link
              href={`/lead/${encodeURIComponent(inc.contact_id)}`}
              style={{ fontWeight: 700, color: "#1d4ed8", fontSize: "0.85rem" }}
            >
              {inc.contact_id}
            </Link>
            <span style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", color: "#64748b" }}>
              {inc.channel} · sent {timeAgo(inc.message_sent_at)}
            </span>
          </div>
          {inc.wrong_dates.map((w, i) => (
            <div key={i} style={{ fontSize: "0.82rem", color: "#991b1b" }}>
              {KIND_LABEL[w.kind]}: told <strong>&quot;{w.raw}&quot;</strong> — should be{" "}
              <strong>{w.expected || "no date (none scheduled)"}</strong>
            </div>
          ))}
          <p style={{ margin: "0.4rem 0 0", fontSize: "0.78rem", color: "#475569", whiteSpace: "pre-wrap" }}>
            {inc.snippet}
          </p>
          {!open && (
            <p style={{ margin: "0.4rem 0 0", fontSize: "0.75rem", color: "#16a34a" }}>
              {inc.status === "corrected" ? "Correction sent" : "Dismissed"} by {inc.resolved_by}{" "}
              {timeAgo(inc.resolved_at)}
              {inc.correction_text ? ` — "${inc.correction_text}"` : ""}
            </p>
          )}
          {err && <p style={{ margin: "0.4rem 0 0", fontSize: "0.78rem", color: "#dc2626" }}>{err}</p>}
        </div>
        {open && (
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <button
              onClick={() => send("email")}
              disabled={busy}
              style={{ ...PRIMARY_BTN, cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1 }}
            >
              Send correction email
            </button>
            {smsEnabled && (
              <button
                onClick={() => send("sms")}
                disabled={busy}
                style={{ ...PRIMARY_BTN, background: "#7c3aed", cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1 }}
              >
                Send correction SMS
              </button>
            )}
            <button onClick={dismiss} disabled={busy} style={{ ...SECONDARY_BTN, cursor: busy ? "wait" : "pointer" }}>
              Dismiss
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function WrongDatesClient() {
  const [tab, setTab] = useState<Tab>("open");
  const [data, setData] = useState<WrongDatesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const load = useCallback(() => {
    fetchWrongDates(tab).then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e)));
  }, [tab]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  async function sendAll(channel: CorrectionChannel) {
    if (!data) return;
    if (!window.confirm(
      `Send the correction ${CHANNEL_LABEL[channel]} to ALL ${data.open_leads} lead(s) now?\n\n${data.correction_previews[channel]}`,
    )) return;
    setBulkBusy(true);
    setError(null);
    try {
      // The server handles a bounded batch per request; keep going until everyone is done,
      // or a pass sends nothing / stops early (failures, daily cap, closed window).
      let sent = 0;
      let failed = 0;
      let blocked = 0;
      let last: BulkSendResult | null = null;
      for (let pass = 0; pass < 300; pass++) {
        const r: BulkSendResult = await sendDateCorrectionAll(channel);
        last = r;
        if (r.shadow) break;
        sent += r.sent;
        failed += r.failed;
        blocked += r.blocked ?? 0;
        setNotice(`Sending… ${sent} ${CHANNEL_LABEL[channel]} sent so far, ${r.remaining} lead(s) remaining.`);
        const progressed = r.sent + r.failed + (r.blocked ?? 0) > 0;
        if (r.remaining === 0 || !progressed || r.stopped_early || r.daily_cap_reached) break;
      }
      setNotice(bulkSummary(channel, sent, failed, blocked, last));
      load();
    } catch (e) {
      // The server may have finished the batch even if the connection broke - say so and refresh.
      setError(`${String(e)} — the connection was interrupted; the page now shows what was actually sent.`);
      load();
    } finally {
      setBulkBusy(false);
    }
  }

  async function sendTest(channel: CorrectionChannel) {
    if (!window.confirm(`Send a TEST ${CHANNEL_LABEL[channel]} correction to your own contact?`)) return;
    setBulkBusy(true);
    setError(null);
    try {
      const r = await sendTestCorrection(channel);
      setNotice(
        r.status === "sent"
          ? `Test ${CHANNEL_LABEL[channel]} written to your contact — check your ${channel === "email" ? "inbox" : "phone"}.`
          : r.status === "skipped"
          ? `Test not sent: ${r.reason}.`
          : "Shadow mode: nothing was sent (GHL writes are off)."
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBulkBusy(false);
    }
  }

  return (
    <div style={{ padding: "1rem 1.5rem 2rem", display: "flex", flexDirection: "column", gap: "0.875rem" }}>
      {error && <div style={{ ...CARD, color: "#dc2626" }}>{error}</div>}

      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "0.75rem" }}>
          {[
            { label: "Open now", value: data.stats.open, color: data.stats.open > 0 ? "#d97706" : "#16a34a" },
            { label: "Closed (last 24h)", value: data.stats.closed_24h, color: "#16a34a" },
            { label: "  · corrected", value: data.stats.corrected_24h, color: "#64748b" },
            { label: "  · dismissed", value: data.stats.dismissed_24h, color: "#64748b" },
            { label: "New (last 24h)", value: data.stats.new_24h, color: "#1e293b" },
            { label: "Emails sent today", value: `${data.email_sent_last_24h} / ${data.email_daily_cap}`, color: "#1e293b" },
          ].map((m) => (
            <div key={m.label} style={CARD}>
              <div style={{ fontSize: "0.68rem", fontWeight: 700, textTransform: "uppercase", color: "#64748b", whiteSpace: "pre" }}>
                {m.label}
              </div>
              <div style={{ fontSize: "1.6rem", fontWeight: 800, color: m.color }}>{m.value}</div>
            </div>
          ))}
        </div>
      )}

      <div style={CARD}>
        <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", color: "#64748b" }}>
          Correct dates (from Settings)
        </div>
        <div style={{ fontSize: "0.9rem", marginTop: 2 }}>
          Next class start: <strong>{data?.expected.class_start || "none scheduled"}</strong> &nbsp;·&nbsp; Next open house:{" "}
          <strong>{data?.expected.open_house || "none scheduled"}</strong>
        </div>
        {data?.correction_previews && (
          <div style={{ fontSize: "0.78rem", color: "#475569", marginTop: 6 }}>
            Correction email preview: &quot;{data.correction_previews.email}&quot;
            {data.sms_enabled && <div>Correction SMS preview: &quot;{data.correction_previews.sms}&quot;</div>}
          </div>
        )}
        {data && !data.sms_enabled && (
          <div style={{ fontSize: "0.74rem", color: "#64748b", marginTop: 6 }}>
            Corrections go out by email (that is how Cora&apos;s follow-ups are delivered). SMS corrections are switched off.
          </div>
        )}
      </div>

      {data?.test_available && (
        <div style={{ ...CARD, display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <span style={{ fontSize: "0.82rem", color: "#475569", flex: 1, minWidth: 240 }}>
            Send a clearly-labelled TEST to your own contact to check a channel still works. No lead is contacted
            and nothing on the list changes.
          </span>
          <button disabled={bulkBusy} onClick={() => sendTest("email")} style={{ ...SECONDARY_BTN, cursor: "pointer" }}>
            Send test email to me
          </button>
          <button disabled={bulkBusy} onClick={() => sendTest("sms")} style={{ ...SECONDARY_BTN, cursor: "pointer" }}>
            Send test SMS to me
          </button>
        </div>
      )}

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        {(["open", "corrected", "dismissed"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "0.3rem 0.75rem", borderRadius: 999, border: "1px solid #cbd5e1", cursor: "pointer",
              background: tab === t ? "#1e293b" : "#fff", color: tab === t ? "#fff" : "#334155",
              fontSize: "0.8rem", textTransform: "capitalize",
            }}
          >
            {t}
            {t === "open" && data ? ` (${data.open_count})` : ""}
          </button>
        ))}
      </div>

      {tab === "open" && data && data.open_leads > 0 && (
        <div style={{ ...CARD, display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <span style={{ fontSize: "0.82rem", color: "#475569", flex: 1, minWidth: 240 }}>
            {data.open_leads} lead(s) have an open incident. One correction per lead, sent a few seconds apart,
            up to {data.email_daily_cap} emails a day. Opted-out leads are skipped automatically.
          </span>
          <button
            disabled={bulkBusy}
            onClick={() => sendAll("email")}
            style={{ ...PRIMARY_BTN, cursor: bulkBusy ? "wait" : "pointer", opacity: bulkBusy ? 0.6 : 1 }}
          >
            {bulkBusy ? "Sending…" : "Send correction email to all"}
          </button>
          {data.sms_enabled && (
            <button
              disabled={bulkBusy}
              onClick={() => sendAll("sms")}
              style={{ ...PRIMARY_BTN, background: "#7c3aed", cursor: bulkBusy ? "wait" : "pointer", opacity: bulkBusy ? 0.6 : 1 }}
            >
              Send correction SMS to all
            </button>
          )}
        </div>
      )}

      {tab === "open" && data && data.open_before_settings_change > 0 && (
        <div style={{ ...CARD, display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <span style={{ fontSize: "0.82rem", color: "#475569", flex: 1, minWidth: 240 }}>
            {data.open_before_settings_change} open incident(s) were sent before the date settings were last
            changed ({data.settings_changed_at ? new Date(data.settings_changed_at).toLocaleString() : "—"}) —
            they used the old values.
          </span>
          <button
            onClick={async () => {
              if (!window.confirm(`Dismiss ${data.open_before_settings_change} incident(s)? No messages will be sent.`)) return;
              try {
                const r = await dismissWrongDatesBulk();
                setNotice(`Dismissed ${r.dismissed} older incident(s).`);
                load();
              } catch (e) {
                setError(String(e));
              }
            }}
            style={{ ...SECONDARY_BTN, cursor: "pointer" }}
          >
            Dismiss all older incidents
          </button>
        </div>
      )}

      {notice && <div style={{ ...CARD, color: "#166534", background: "#f0fdf4" }}>{notice}</div>}

      {data && data.incidents.length === 0 && (
        <div style={{ ...CARD, color: "#64748b" }}>
          {tab === "open" ? "No wrong-date messages — all clear." : `No ${tab} incidents.`}
        </div>
      )}

      {data?.incidents.map((inc) => (
        <IncidentRow
          key={inc.id}
          inc={inc}
          previews={data.correction_previews}
          smsEnabled={data.sms_enabled}
          onChanged={(m) => {
            setNotice(m);
            load();
          }}
        />
      ))}
    </div>
  );
}
