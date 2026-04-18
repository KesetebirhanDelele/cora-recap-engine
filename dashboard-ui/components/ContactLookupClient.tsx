"use client";

import { useState, useEffect } from "react";
import { fetchLeadDetail } from "@/lib/api";
import type {
  LeadDetailResponse,
  CallEventRecord,
  ShadowActionRecord,
  ScheduledJobRecord,
  OutboundMessageRecord,
  ContactExceptionRecord,
} from "@/types";

const SECTION_STYLE: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  overflow: "hidden",
};

const SECTION_HEAD: React.CSSProperties = {
  padding: "0.625rem 1rem",
  background: "#f8fafc",
  borderBottom: "1px solid #e2e8f0",
  fontWeight: 700,
  fontSize: "0.85rem",
  color: "#1e293b",
};

const HEAD: React.CSSProperties = {
  padding: "0.45rem 0.75rem",
  background: "#f8fafc",
  borderBottom: "2px solid #e2e8f0",
  fontSize: "0.72rem",
  fontWeight: 700,
  color: "#64748b",
  textTransform: "uppercase" as const,
  letterSpacing: "0.06em",
  whiteSpace: "nowrap",
};

const CELL: React.CSSProperties = {
  padding: "0.45rem 0.75rem",
  borderBottom: "1px solid #f1f5f9",
  fontSize: "0.8rem",
  color: "#334155",
  maxWidth: 260,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const EMPTY: React.CSSProperties = {
  padding: "1rem",
  fontSize: "0.82rem",
  color: "#94a3b8",
  textAlign: "center",
};

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "America/Chicago",
  }) + " CST";
}

function JsonCell({ val }: { val: unknown }) {
  if (val === null || val === undefined) return <span style={{ color: "#94a3b8" }}>—</span>;
  if (typeof val === "string") return <span>{val}</span>;
  return (
    <span
      title={JSON.stringify(val, null, 2)}
      style={{ cursor: "help", color: "#475569", fontFamily: "monospace", fontSize: "0.75rem" }}
    >
      {JSON.stringify(val).slice(0, 60)}{JSON.stringify(val).length > 60 ? "…" : ""}
    </span>
  );
}

function StatusBadge({ val, color }: { val: string; color?: string }) {
  const c = color ?? "#64748b";
  return (
    <span style={{
      display: "inline-block", padding: "0.1rem 0.45rem",
      borderRadius: 4, background: c + "18", color: c,
      fontWeight: 600, fontSize: "0.75rem",
    }}>
      {val}
    </span>
  );
}

const SEV_COLORS: Record<string, string> = { critical: "#ef4444", warning: "#f59e0b" };
const JOB_STATUS_COLORS: Record<string, string> = {
  pending: "#3b82f6", claimed: "#f59e0b", running: "#f59e0b",
  completed: "#22c55e", failed: "#ef4444", cancelled: "#94a3b8",
};

// ── Lead State ────────────────────────────────────────────────────────────────

function LeadStateSection({ data }: { data: LeadDetailResponse["lead_state"] }) {
  const fields: [string, unknown][] = [
    ["contact_id", data.contact_id],
    ["campaign_name", data.campaign_name ?? "—"],
    ["ai_campaign_value", data.ai_campaign_value ?? "—"],
    ["status", data.status],
    ["do_not_call", String(data.do_not_call)],
    ["next_action_at", fmtTs(data.next_action_at)],
    ["version", data.version],
    ["updated_at", fmtTs(data.updated_at)],
  ];
  return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Lead State</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {fields.map(([k]) => <th key={k} style={HEAD}>{k}</th>)}
            </tr>
          </thead>
          <tbody>
            <tr>
              {fields.map(([k, v]) => (
                <td key={k} style={CELL}>{String(v)}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Call Events ───────────────────────────────────────────────────────────────

function CallEventsSection({ rows }: { rows: CallEventRecord[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(callId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(callId) ? next.delete(callId) : next.add(callId);
      return next;
    });
  }

  if (rows.length === 0) return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Call Events</div>
      <p style={EMPTY}>No call events.</p>
    </div>
  );
  return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Call Events ({rows.length})</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["call_id", "status", "duration_s", "transcript_preview", "call_summary", "created_at"].map((h) => (
                <th key={h} style={HEAD}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <>
                <tr key={r.call_id} style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}>
                  <td style={{ ...CELL, fontFamily: "monospace", fontSize: "0.75rem" }}>{r.call_id}</td>
                  <td style={CELL}>
                    <StatusBadge
                      val={r.status}
                      color={r.status === "completed" ? "#22c55e" : r.status === "failed" ? "#ef4444" : undefined}
                    />
                  </td>
                  <td style={CELL}>{r.duration_seconds != null ? `${r.duration_seconds}s` : "—"}</td>
                  <td style={{ ...CELL, maxWidth: 300, fontStyle: r.transcript_preview ? "normal" : "italic", color: r.transcript_preview ? "#334155" : "#94a3b8" }}>
                    {r.transcript_preview ?? "(none)"}
                  </td>
                  <td style={{ ...CELL, maxWidth: 300 }}>
                    {r.student_summary ? (
                      <button
                        onClick={() => toggle(r.call_id)}
                        style={{
                          background: "none", border: "none", padding: 0,
                          fontSize: "0.78rem", color: "#2563eb", cursor: "pointer",
                          textDecoration: "underline", textDecorationStyle: "dotted",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {expanded.has(r.call_id) ? "▲ hide summary" : "▼ view summary"}
                      </button>
                    ) : (
                      <span style={{ color: "#94a3b8", fontStyle: "italic", fontSize: "0.78rem" }}>(none)</span>
                    )}
                  </td>
                  <td style={{ ...CELL, color: "#475569" }}>{fmtTs(r.created_at)}</td>
                </tr>

                {/* Expanded summary row */}
                {r.student_summary && expanded.has(r.call_id) && (
                  <tr key={r.call_id + "-summary"} style={{ background: "#f0f9ff" }}>
                    <td colSpan={6} style={{ padding: "0.625rem 0.75rem", borderBottom: "1px solid #e0f2fe" }}>
                      <div style={{ fontSize: "0.78rem", fontWeight: 700, color: "#0369a1", marginBottom: "0.3rem" }}>
                        AI Call Summary
                      </div>
                      <div style={{
                        fontSize: "0.82rem", color: "#1e293b", lineHeight: 1.55,
                        whiteSpace: "pre-wrap", maxHeight: 200, overflowY: "auto",
                      }}>
                        {r.student_summary}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Shadow Actions ────────────────────────────────────────────────────────────

function ShadowActionsSection({ rows }: { rows: ShadowActionRecord[] }) {
  if (rows.length === 0) return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Shadow Actions</div>
      <p style={EMPTY}>No shadow actions.</p>
    </div>
  );
  return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Shadow Actions ({rows.length})</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["action_type", "payload", "created_at"].map((h) => <th key={h} style={HEAD}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}>
                <td style={CELL}><StatusBadge val={r.action_type} color="#8b5cf6" /></td>
                <td style={{ ...CELL, maxWidth: 360 }}><JsonCell val={r.payload} /></td>
                <td style={{ ...CELL, color: "#475569" }}>{fmtTs(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Scheduled Jobs ────────────────────────────────────────────────────────────

function ScheduledJobsSection({ rows }: { rows: ScheduledJobRecord[] }) {
  if (rows.length === 0) return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Scheduled Jobs</div>
      <p style={EMPTY}>No scheduled jobs.</p>
    </div>
  );
  return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Scheduled Jobs ({rows.length})</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["job_type", "status", "run_at", "payload_json", "created_at"].map((h) => (
                <th key={h} style={HEAD}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}>
                <td style={CELL}>{r.job_type}</td>
                <td style={CELL}>
                  <StatusBadge val={r.status} color={JOB_STATUS_COLORS[r.status] ?? "#64748b"} />
                </td>
                <td style={{ ...CELL, color: "#475569" }}>{fmtTs(r.run_at)}</td>
                <td style={{ ...CELL, maxWidth: 320 }}><JsonCell val={r.payload_json} /></td>
                <td style={{ ...CELL, color: "#475569" }}>{fmtTs(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Outbound Messages ─────────────────────────────────────────────────────────

function OutboundMessagesSection({ rows }: { rows: OutboundMessageRecord[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggle(idx: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  }

  if (rows.length === 0) return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Outbound Messages</div>
      <p style={EMPTY}>No outbound messages.</p>
    </div>
  );

  const CHANNEL_COLOR: Record<string, string> = { sms: "#8b5cf6", email: "#ec4899" };

  return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Outbound Messages ({rows.length})</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["channel", "status", "subject / preview", "content", "created_at"].map((h) => (
                <th key={h} style={HEAD}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <>
                <tr key={i} style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}>
                  <td style={CELL}>
                    <StatusBadge val={r.channel.toUpperCase()} color={CHANNEL_COLOR[r.channel] ?? "#06b6d4"} />
                  </td>
                  <td style={CELL}>
                    <StatusBadge
                      val={r.status}
                      color={r.status === "sent" ? "#22c55e" : r.status === "failed" ? "#ef4444" : r.status === "shadow" ? "#f59e0b" : undefined}
                    />
                  </td>
                  <td style={{ ...CELL, maxWidth: 280 }}>
                    {r.subject ? (
                      <span title={r.subject} style={{ fontWeight: 600, color: "#1e293b" }}>
                        {r.subject.length > 60 ? r.subject.slice(0, 60) + "…" : r.subject}
                      </span>
                    ) : r.body ? (
                      <span style={{ color: "#64748b", fontStyle: "italic" }}>
                        {r.body.slice(0, 60)}{r.body.length > 60 ? "…" : ""}
                      </span>
                    ) : (
                      <span style={{ color: "#94a3b8", fontStyle: "italic" }}>(none)</span>
                    )}
                  </td>
                  <td style={{ ...CELL }}>
                    {r.body ? (
                      <button
                        onClick={() => toggle(i)}
                        style={{
                          background: "none", border: "none", padding: 0,
                          fontSize: "0.78rem", color: "#2563eb", cursor: "pointer",
                          textDecoration: "underline", textDecorationStyle: "dotted",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {expanded.has(i) ? "▲ hide" : "▼ view full"}
                      </button>
                    ) : (
                      <span style={{ color: "#94a3b8", fontStyle: "italic", fontSize: "0.78rem" }}>(empty)</span>
                    )}
                  </td>
                  <td style={{ ...CELL, color: "#475569" }}>{fmtTs(r.created_at)}</td>
                </tr>

                {/* Expanded full message body */}
                {r.body && expanded.has(i) && (
                  <tr key={`msg-${i}-body`} style={{ background: "#fdf4ff" }}>
                    <td colSpan={5} style={{ padding: "0.625rem 0.75rem", borderBottom: "1px solid #e9d5ff" }}>
                      {r.subject && (
                        <div style={{ fontSize: "0.78rem", fontWeight: 700, color: "#7c3aed", marginBottom: "0.3rem" }}>
                          Subject: {r.subject}
                        </div>
                      )}
                      <div style={{
                        fontSize: "0.82rem", color: "#1e293b", lineHeight: 1.55,
                        whiteSpace: "pre-wrap", maxHeight: 260, overflowY: "auto",
                        background: "#fff", border: "1px solid #e9d5ff",
                        borderRadius: 4, padding: "0.5rem 0.75rem",
                      }}>
                        {r.body}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Exceptions ────────────────────────────────────────────────────────────────

function ExceptionsSection({ rows }: { rows: ContactExceptionRecord[] }) {
  if (rows.length === 0) return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Exceptions</div>
      <p style={EMPTY}>No exceptions.</p>
    </div>
  );
  return (
    <div style={SECTION_STYLE}>
      <div style={SECTION_HEAD}>Exceptions ({rows.length})</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["type", "severity", "status", "context_json", "created_at"].map((h) => (
                <th key={h} style={HEAD}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}>
                <td style={CELL}>{r.type}</td>
                <td style={CELL}><StatusBadge val={r.severity} color={SEV_COLORS[r.severity]} /></td>
                <td style={CELL}>
                  <StatusBadge val={r.status} color={r.status === "open" ? "#ef4444" : r.status === "resolved" ? "#22c55e" : "#94a3b8"} />
                </td>
                <td style={{ ...CELL, maxWidth: 320 }}><JsonCell val={r.context_json} /></td>
                <td style={{ ...CELL, color: "#475569" }}>{fmtTs(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface ContactLookupClientProps {
  /** When provided, auto-fetch this contact on mount (used by drill-down pages). */
  contactId?: string;
}

export default function ContactLookupClient({ contactId: externalContactId }: ContactLookupClientProps = {}) {
  const [input, setInput] = useState(externalContactId ?? "");
  const [data, setData] = useState<LeadDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastQueried, setLastQueried] = useState<string | null>(null);

  async function lookup(cid?: string) {
    const id = (cid ?? input).trim();
    if (!id) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const result = await fetchLeadDetail(id);
      setData(result);
      setLastQueried(id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.includes("404") ? `No lead found for "${id}".` : msg);
    } finally {
      setLoading(false);
    }
  }

  // Auto-fetch when a contactId is injected externally (drill-down mode)
  useEffect(() => {
    if (externalContactId) lookup(externalContactId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalContactId]);

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === "Enter") lookup();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      {/* ── Search bar — hidden in drill-down mode ── */}
      {!externalContactId && (
        <div style={{
          display: "flex", gap: "0.75rem", alignItems: "center",
          background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "0.875rem 1rem",
        }}>
          <input
            type="text"
            placeholder="e.g. sim-sc1-001 or +15551110001"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            style={{
              flex: 1, border: "1px solid #e2e8f0", borderRadius: 6,
              padding: "0.4rem 0.75rem", fontSize: "0.9rem", color: "#1e293b",
              background: "#fff", outline: "none",
            }}
          />
          <button
            onClick={() => lookup()}
            disabled={loading || !input.trim()}
            style={{
              padding: "0.4rem 1.1rem",
              background: "#1e293b",
              color: "#fff", border: "none", borderRadius: 6,
              fontSize: "0.85rem", fontWeight: 600,
              cursor: loading || !input.trim() ? "not-allowed" : "pointer",
              opacity: loading || !input.trim() ? 0.5 : 1,
            }}
          >
            {loading ? "Looking up…" : "Look up"}
          </button>
        </div>
      )}

      {/* ── Hint ── */}
      {!externalContactId && !data && !loading && !error && (
        <div style={{
          background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "2rem", textAlign: "center", color: "#64748b", fontSize: "0.9rem",
        }}>
          Enter a contact ID or E.164 phone number above to inspect all data for that contact.
        </div>
      )}

      {/* ── Loading spinner ── */}
      {loading && (
        <div style={{
          background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: "2rem", textAlign: "center", color: "#64748b", fontSize: "0.9rem",
        }}>
          Loading…
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div style={{
          background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8,
          padding: "0.75rem 1rem", color: "#991b1b", fontSize: "0.85rem",
        }}>
          {error}
        </div>
      )}

      {/* ── Results ── */}
      {data && (
        <>
          {!externalContactId && (
            <div style={{
              fontSize: "0.8rem", color: "#64748b", padding: "0 0.25rem",
            }}>
              Showing all data for <strong style={{ color: "#1e293b" }}>{lastQueried}</strong>
            </div>
          )}

          {data.lead_state ? (
            <LeadStateSection data={data.lead_state} />
          ) : (
            <div style={SECTION_STYLE}>
              <div style={SECTION_HEAD}>Lead State</div>
              <p style={{ ...EMPTY, color: "#f59e0b" }}>
                No lead state — this contact has call events but never progressed past telephony (e.g. failed call before connection).
              </p>
            </div>
          )}
          <CallEventsSection rows={data.call_events} />
          <ShadowActionsSection rows={data.shadow_actions} />
          <ScheduledJobsSection rows={data.scheduled_jobs} />
          <OutboundMessagesSection rows={data.outbound_messages} />
          <ExceptionsSection rows={data.exceptions} />
        </>
      )}
    </div>
  );
}
