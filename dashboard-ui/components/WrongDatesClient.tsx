"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchWrongDates,
  sendDateCorrection,
  dismissWrongDate,
  dismissWrongDatesBulk,
  sendDateCorrectionAll,
  type BulkSendResult,
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
  preview,
  onChanged,
}: {
  inc: WrongDateIncident;
  preview: string | null;
  onChanged: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const open = inc.status === "open";

  async function send() {
    if (!window.confirm(`Send this correction SMS to ${inc.contact_id}?\n\n${preview ?? ""}`)) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await sendDateCorrection(inc.id);
      onChanged(
        r.status === "shadow"
          ? "Shadow mode: nothing was sent (GHL writes are off). Incident left open."
          : `Correction sent to ${inc.contact_id}.` +
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
              <strong>{w.expected}</strong>
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
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              onClick={send}
              disabled={busy || !preview}
              style={{
                background: "#2563eb", color: "#fff", border: 0, borderRadius: 6,
                padding: "0.45rem 0.8rem", fontWeight: 600,
                cursor: busy ? "wait" : "pointer", opacity: busy || !preview ? 0.6 : 1,
              }}
            >
              Send correction SMS
            </button>
            <button
              onClick={dismiss}
              disabled={busy}
              style={{
                background: "#fff", color: "#475569", border: "1px solid #cbd5e1",
                borderRadius: 6, padding: "0.45rem 0.8rem", cursor: busy ? "wait" : "pointer",
              }}
            >
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
    fetchWrongDates(tab).then(setData).catch((e) => setError(String(e)));
  }, [tab]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div style={{ padding: "1rem 1.5rem 2rem", display: "flex", flexDirection: "column", gap: "0.875rem" }}>
      {error && <div style={{ ...CARD, color: "#dc2626" }}>{error}</div>}

      <div style={CARD}>
        <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", color: "#64748b" }}>
          Correct dates (from Settings)
        </div>
        <div style={{ fontSize: "0.9rem", marginTop: 2 }}>
          Next class start: <strong>{data?.expected.class_start || "—"}</strong> &nbsp;·&nbsp; Next open house:{" "}
          <strong>{data?.expected.open_house || "—"}</strong>
        </div>
        {data?.correction_preview && (
          <div style={{ fontSize: "0.78rem", color: "#475569", marginTop: 6 }}>
            Correction SMS preview: &quot;{data.correction_preview}&quot;
          </div>
        )}
      </div>

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
            {data.open_leads} lead(s) have an open incident. One correction SMS per lead
            All are sent with one click.
          </span>
          <button
            disabled={bulkBusy}
            onClick={async () => {
              if (!window.confirm(`Send the correction SMS to ALL ${data.open_leads} lead(s) now?\n\n${data.correction_preview ?? ""}`)) return;
              setBulkBusy(true);
              setError(null);
              try {
                // The server handles a bounded batch per request; keep going until
                // everyone is done, or a pass sends nothing / stops early (failures).
                let sent = 0;
                let failed = 0;
                let last: BulkSendResult | null = null;
                for (let pass = 0; pass < 50; pass++) {
                  last = await sendDateCorrectionAll();
                  if (last.shadow) break;
                  sent += last.sent;
                  failed += last.failed;
                  setNotice(`Sending… ${sent} sent so far, ${last.remaining} lead(s) remaining.`);
                  if (last.remaining === 0 || last.sent === 0 || last.stopped_early) break;
                }
                setNotice(
                  last?.shadow
                    ? "Shadow mode: nothing was sent (GHL writes are off). Incidents left open."
                    : `Done: ${sent} correction(s) sent` +
                      (failed ? `, ${failed} failed (left open)` : "") + "." +
                      (last?.stopped_early ? " Stopped early after repeated failures: " + (last.errors ?? []).join("; ") : "")
                );
                load();
              } catch (e) {
                setError(String(e));
              } finally {
                setBulkBusy(false);
              }
            }}
            style={{ background: "#2563eb", color: "#fff", border: 0, borderRadius: 6, padding: "0.45rem 0.8rem", fontWeight: 600, cursor: bulkBusy ? "wait" : "pointer", opacity: bulkBusy ? 0.6 : 1 }}
          >
            {bulkBusy ? "Sending…" : "Send correction SMS to all"}
          </button>
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
            style={{ background: "#fff", color: "#475569", border: "1px solid #cbd5e1", borderRadius: 6, padding: "0.45rem 0.8rem", cursor: "pointer" }}
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
          preview={data.correction_preview}
          onChanged={(m) => {
            setNotice(m);
            load();
          }}
        />
      ))}
    </div>
  );
}
