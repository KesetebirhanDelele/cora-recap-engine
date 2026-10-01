"use client";

import { useCallback, useEffect, useState } from "react";
import {
  applyOptout, applyOptoutBatch, dismissOptout, fetchOptouts, undoOptout,
  type OptoutRow, type OptoutsResponse,
} from "@/lib/api";

const CARD: React.CSSProperties = {
  background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "0.875rem 1rem",
  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
};
const PRIMARY: React.CSSProperties = { background: "#dc2626", color: "#fff", border: 0, borderRadius: 6, padding: "0.35rem 0.7rem", fontWeight: 600, cursor: "pointer" };
const SECONDARY: React.CSSProperties = { background: "#fff", color: "#475569", border: "1px solid #cbd5e1", borderRadius: 6, padding: "0.35rem 0.7rem", cursor: "pointer" };
const CHANNELS = ["call", "sms", "email"] as const;
const SOURCE_LABEL: Record<string, string> = {
  call: "Phone call", sms_reply: "SMS reply", email_reply: "Email reply", reconcile: "Marked do-not-call in Cora, no DND in GHL",
};

function ago(iso: string | null): string {
  if (!iso) return "";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return `${Math.max(m, 0)} min ago`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.floor(h / 24)} d ago`;
}

function ReviewRow({ r, onDone }: { r: OptoutRow; onDone: () => void }) {
  const [scope, setScope] = useState<string[]>(r.scope ? r.scope.split(",") : [...CHANNELS]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toggle = (c: string) => setScope((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]));
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); onDone(); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const isDnd = r.kind === "dnd";
  return (
    <div style={{ borderTop: "1px solid #e2e8f0", padding: "0.6rem 0" }}>
      <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
        {SOURCE_LABEL[r.source] ?? r.source} · {ago(r.created_at)} · contact {r.contact_id.slice(0, 8)}… · {r.kind.replace("_", " ")}
      </div>
      {r.excerpt && <div style={{ margin: "0.25rem 0", fontStyle: "italic" }}>&ldquo;{r.excerpt}&rdquo;</div>}
      <div style={{ fontSize: "0.8rem", color: "#475569" }}>{r.phrase}{r.reason ? ` — ${r.reason}` : ""}</div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
        {isDnd && CHANNELS.map((c) => (
          <label key={c} style={{ fontSize: "0.85rem" }}>
            <input type="checkbox" checked={scope.includes(c)} onChange={() => toggle(c)} /> {c}
          </label>
        ))}
        <button style={PRIMARY} disabled={busy || (isDnd && scope.length === 0)}
                onClick={() => run(() => applyOptout(r.id, isDnd ? scope : undefined))}>
          {isDnd ? "Apply DND" : "Close lead"}
        </button>
        <button style={SECONDARY} disabled={busy} onClick={() => run(() => dismissOptout(r.id))}>Not an opt-out</button>
        {err && <span style={{ color: "#dc2626", fontSize: "0.8rem" }}>{err}</span>}
      </div>
    </div>
  );
}

export default function OptoutsClient() {
  const [d, setD] = useState<OptoutsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [batch, setBatch] = useState<string | null>(null);
  const load = useCallback(() => fetchOptouts().then((x) => { setD(x); setErr(null); }).catch((e) => setErr(String(e))), []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);

  const applyAllReconcile = async () => {
    if (!window.confirm("Apply the proposed DND in GHL to every lead on this list? You can undo each one afterwards.")) return;
    let applied = 0, failed = 0;
    try {
      for (let i = 0; i < 60; i++) {
        setBatch(`Applying… ${applied} done`);
        const r = await applyOptoutBatch("reconcile");
        applied += r.applied; failed += r.failed;
        if (r.remaining === 0 || (r.applied === 0 && r.failed > 0 && i > 2)) break;
      }
      setBatch(`Done: ${applied} applied${failed ? `, ${failed} failed` : ""}.`);
    } catch (e) { setBatch(String(e)); }
    load();
  };

  if (err) return <div style={{ ...CARD, color: "#dc2626" }}>Could not load opt-outs: {err}</div>;
  if (!d) return <div style={CARD}>Loading…</div>;
  const reconcile = d.review.filter((r) => r.source === "reconcile");
  const others = d.review.filter((r) => r.source !== "reconcile");

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "0.75rem" }}>
        {([["Waiting for you", d.counts.review ?? 0], ["DND applied", d.counts.applied ?? 0],
           ["Already DND in GHL", d.counts.ok ?? 0], ["Still being checked", d.reconcile_pending]] as const).map(([k, v]) => (
          <div key={k} style={CARD}>
            <div style={{ fontSize: "0.8rem", color: "#64748b" }}>{k}</div>
            <div style={{ fontSize: "1.6rem", fontWeight: 800 }}>{v}</div>
          </div>
        ))}
      </div>

      <div style={CARD}>
        <strong>Needs your decision ({others.length})</strong>
        <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
          Clear opt-outs (&ldquo;stop calling&rdquo;, &ldquo;remove me&rdquo;, STOP…) are applied automatically by the lead&apos;s own wording.
          These were unclear or contradictory. While they wait, Cora will not message that lead on the channel.
        </div>
        {others.length === 0 && <div style={{ color: "#16a34a", marginTop: 6 }}>Nothing waiting.</div>}
        {others.map((r) => <ReviewRow key={r.id} r={r} onDone={load} />)}
      </div>

      <div style={CARD}>
        <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <strong>Marked do-not-call in Cora but no DND in GHL ({reconcile.length})</strong>
          {reconcile.length > 0 && <button style={PRIMARY} onClick={applyAllReconcile}>Apply DND to all {reconcile.length}</button>}
        </div>
        {batch && <div style={{ margin: "0.4rem 0", fontSize: "0.85rem" }}>{batch}</div>}
        <div style={{ fontSize: "0.8rem", color: "#64748b" }}>
          Scope is taken from what the lead said on the call (all channels when no specific channel was named).
        </div>
        {reconcile.map((r) => <ReviewRow key={r.id} r={r} onDone={load} />)}
      </div>

      <div style={CARD}>
        <strong>Recently applied</strong>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: "0.82rem", borderCollapse: "collapse", marginTop: 6 }}>
            <thead><tr style={{ textAlign: "left", color: "#64748b" }}><th>When</th><th>Source</th><th>Channels</th><th>By</th><th>Said</th><th></th></tr></thead>
            <tbody>
              {d.applied.map((r) => (
                <tr key={r.id} style={{ borderTop: "1px solid #e2e8f0" }}>
                  <td>{ago(r.resolved_at)}</td><td>{SOURCE_LABEL[r.source] ?? r.source}</td>
                  <td>{r.kind === "dnd" ? r.scope.replace(/,/g, " + ") : r.kind.replace("_", " ")}</td>
                  <td>{r.decided_by}</td><td>{r.excerpt ?? r.phrase}</td>
                  <td>{r.kind === "dnd" && <button style={SECONDARY} onClick={async () => { await undoOptout(r.id); load(); }}>Undo</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {d.failed.length > 0 && <div style={{ marginTop: 8, color: "#dc2626", fontSize: "0.85rem" }}>
          {d.failed.length} could not be applied (see reason): {d.failed.slice(0, 3).map((f) => f.reason).join(" | ")}
        </div>}
      </div>
    </div>
  );
}
