"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchSettings, saveSettings } from "@/lib/api";
import type { AuditLogEntry } from "@/types";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function daysFromStr(s: string): string[] {
  return s
    .split(",")
    .map((p) => p.trim())
    .filter((p) => /^\d$/.test(p) && Number(p) < 7)
    .map((p) => DAY_LABELS[Number(p)]);
}

function daysToStr(selected: string[]): string {
  return DAY_LABELS.filter((d) => selected.includes(d))
    .map((d) => String(DAY_LABELS.indexOf(d)))
    .join(",");
}

function getInt(cfg: Record<string, string>, key: string, fallback: number): number {
  const v = cfg[key];
  if (v === undefined) return fallback;
  const n = parseInt(v, 10);
  return isNaN(n) ? fallback : n;
}

function getBool(cfg: Record<string, string>, key: string, fallback: boolean): boolean {
  const v = cfg[key];
  if (v === undefined) return fallback;
  return ["true", "1"].includes(v.toLowerCase());
}

function getString(cfg: Record<string, string>, key: string, fallback: string): string {
  return cfg[key] ?? fallback;
}

// ── UI primitives ──────────────────────────────────────────────────────────────

const LABEL: React.CSSProperties = {
  fontSize: "0.75rem", fontWeight: 600, color: "#64748b", display: "block", marginBottom: 4,
};

const INPUT: React.CSSProperties = {
  border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.4rem 0.65rem",
  fontSize: "0.875rem", color: "#1e293b", background: "#fff", width: "100%",
  boxSizing: "border-box" as const,
};

const NUM_INPUT: React.CSSProperties = {
  ...INPUT, width: 100,
};

const SECTION_CARD: React.CSSProperties = {
  background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
  padding: "1.25rem 1.25rem 1rem",
  display: "flex", flexDirection: "column" as const, gap: "0.875rem",
};

const SECTION_TITLE: React.CSSProperties = {
  fontSize: "0.92rem", fontWeight: 700, color: "#1e293b", margin: 0,
};

const SECTION_CAPTION: React.CSSProperties = {
  fontSize: "0.78rem", color: "#64748b", margin: "0.15rem 0 0",
};

const DIVIDER: React.CSSProperties = {
  height: 1, background: "#e2e8f0", margin: "0.25rem 0",
};

function DayPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
      {DAY_LABELS.map((d) => {
        const active = value.includes(d);
        return (
          <button
            key={d}
            type="button"
            onClick={() => onChange(active ? value.filter((x) => x !== d) : [...value, d])}
            style={{
              padding: "0.25rem 0.6rem", borderRadius: 5, fontSize: "0.78rem", fontWeight: 600,
              border: "1px solid",
              borderColor: active ? "#3b82f6" : "#e2e8f0",
              background: active ? "#eff6ff" : "#f8fafc",
              color: active ? "#1d4ed8" : "#64748b",
              cursor: "pointer",
            }}
          >
            {d}
          </button>
        );
      })}
    </div>
  );
}

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  }) + " UTC";
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function SettingsClient() {
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [audit, setAudit] = useState<AuditLogEntry[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showAudit, setShowAudit] = useState(false);

  // ── Form state ──
  const [operatorId, setOperatorId] = useState("dashboard");
  const [dashboardToken, setDashboardToken] = useState(() =>
    typeof window !== "undefined" ? localStorage.getItem("dashboard_token") ?? "" : ""
  );

  const [nlDays, setNlDays] = useState<string[]>(DAY_LABELS);
  const [nlStart, setNlStart] = useState(8);
  const [nlEnd, setNlEnd] = useState(18);

  const [clDays, setClDays] = useState<string[]>(["Mon", "Tue", "Wed", "Thu", "Fri"]);
  const [clStart, setClStart] = useState(9);
  const [clEnd, setClEnd] = useState(17);

  const [coldTNone, setColdTNone] = useState(120);
  const [coldT0, setColdT0] = useState(1440);
  const [coldT1, setColdT1] = useState(2880);
  const [coldFinalize, setColdFinalize] = useState(false);

  const [newTNone, setNewTNone] = useState(120);
  const [newT0, setNewT0] = useState(1440);
  const [newT1, setNewT1] = useState(2880);
  const [newFinalize, setNewFinalize] = useState(true);

  const [smsDelay, setSmsDelay] = useState(30);
  const [emailDelay, setEmailDelay] = useState(2);

  const [brandName, setBrandName] = useState("Colaberry");
  const [senderName, setSenderName] = useState("Cora from Colaberry");
  const [replyToEmail, setReplyToEmail] = useState("admissions@colaberry.com");
  const [unsubscribeText, setUnsubscribeText] = useState("Text STOP to stop alerts");
  const [nextClassStart, setNextClassStart] = useState("upcoming");
  const [liveOpenHouseLink, setLiveOpenHouseLink] = useState("");
  const [explainerVideoLink, setExplainerVideoLink] = useState("");

  const [admissionsAssistants, setAdmissionsAssistants] = useState<Array<{ name: string; ghl_id: string }>>([]);

  const applyConfig = useCallback((c: Record<string, string>) => {
    setCfg(c);
    setNlDays(daysFromStr(getString(c, "new_lead_active_days", "0,1,2,3,4,5,6")));
    setNlStart(getInt(c, "new_lead_active_start_hour", 8));
    setNlEnd(getInt(c, "new_lead_active_end_hour", 18));
    setClDays(daysFromStr(getString(c, "cold_lead_active_days", "0,1,2,3,4")));
    setClStart(getInt(c, "cold_lead_active_start_hour", 9));
    setClEnd(getInt(c, "cold_lead_active_end_hour", 17));
    setColdTNone(getInt(c, "cold_vm_tier_none_delay_minutes", 120));
    setColdT0(getInt(c, "cold_vm_tier_0_delay_minutes", 1440));
    setColdT1(getInt(c, "cold_vm_tier_1_delay_minutes", 2880));
    setColdFinalize(getBool(c, "cold_vm_tier_2_finalizes", false));
    setNewTNone(getInt(c, "new_vm_tier_none_delay_minutes", 120));
    setNewT0(getInt(c, "new_vm_tier_0_delay_minutes", 1440));
    setNewT1(getInt(c, "new_vm_tier_1_delay_minutes", 2880));
    setNewFinalize(getBool(c, "new_vm_tier_2_finalize", true));
    setSmsDelay(getInt(c, "sms_followup_delay_minutes", 30));
    setEmailDelay(getInt(c, "email_followup_delay_days", 2));
    setBrandName(getString(c, "brand_name", "Colaberry"));
    setSenderName(getString(c, "sender_name", "Cora from Colaberry"));
    setReplyToEmail(getString(c, "reply_to_email", "admissions@colaberry.com"));
    setUnsubscribeText(getString(c, "unsubscribe_text", "Text STOP to stop alerts"));
    setNextClassStart(getString(c, "next_class_start", "upcoming"));
    setLiveOpenHouseLink(getString(c, "live_open_house_link", ""));
    setExplainerVideoLink(getString(c, "explainer_open_house_video_link", ""));
    try {
      const raw = c["admissions_assistants"];
      setAdmissionsAssistants(raw ? JSON.parse(raw) : []);
    } catch {
      setAdmissionsAssistants([]);
    }
  }, []);

  useEffect(() => {
    fetchSettings()
      .then((data) => {
        applyConfig(data.config);
        setAudit(data.audit_log);
      })
      .catch((e) => setLoadError(String(e)));
  }, [applyConfig]);

  // ── Validation ──
  const errors: string[] = [];
  if (nlDays.length === 0) errors.push("New Lead: select at least one active day.");
  if (nlStart >= nlEnd) errors.push("New Lead: start hour must be less than end hour.");
  if (clDays.length === 0) errors.push("Cold Lead: select at least one active day.");
  if (clStart >= clEnd) errors.push("Cold Lead: start hour must be less than end hour.");
  if (!brandName.trim()) errors.push("Brand name cannot be empty.");
  if (!senderName.trim()) errors.push("Sender name cannot be empty.");

  function handleSaveToken() {
    if (typeof window !== "undefined") {
      if (dashboardToken.trim()) {
        localStorage.setItem("dashboard_token", dashboardToken.trim());
      } else {
        localStorage.removeItem("dashboard_token");
      }
    }
    setSaveMsg({ ok: true, text: "Token saved to browser. Write actions will now include it." });
  }

  async function handleSave() {
    if (errors.length > 0 || !operatorId.trim()) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      await saveSettings({
        operator_id: operatorId.trim(),
        values: {
          new_lead_active_days: daysToStr(nlDays),
          new_lead_active_start_hour: String(nlStart),
          new_lead_active_end_hour: String(nlEnd),
          cold_lead_active_days: daysToStr(clDays),
          cold_lead_active_start_hour: String(clStart),
          cold_lead_active_end_hour: String(clEnd),
          cold_vm_tier_none_delay_minutes: String(coldTNone),
          cold_vm_tier_0_delay_minutes: String(coldT0),
          cold_vm_tier_1_delay_minutes: String(coldT1),
          cold_vm_tier_2_finalizes: String(coldFinalize),
          new_vm_tier_none_delay_minutes: String(newTNone),
          new_vm_tier_0_delay_minutes: String(newT0),
          new_vm_tier_1_delay_minutes: String(newT1),
          new_vm_tier_2_finalize: String(newFinalize),
          sms_followup_delay_minutes: String(smsDelay),
          email_followup_delay_days: String(emailDelay),
          brand_name: brandName.trim(),
          sender_name: senderName.trim(),
          reply_to_email: replyToEmail.trim(),
          unsubscribe_text: unsubscribeText.trim(),
          next_class_start: nextClassStart.trim(),
          live_open_house_link: liveOpenHouseLink.trim(),
          explainer_open_house_video_link: explainerVideoLink.trim(),
          admissions_assistants: JSON.stringify(admissionsAssistants),
        },
      });
      setSaveMsg({ ok: true, text: "Settings saved. Changes are now live." });
      // Refresh audit log
      fetchSettings().then((data) => setAudit(data.audit_log)).catch(() => null);
    } catch (e) {
      setSaveMsg({ ok: false, text: String(e) });
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div style={{
        background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8,
        padding: "1rem", color: "#991b1b", fontSize: "0.85rem",
      }}>
        Failed to load settings: {loadError}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>

      {/* ── Dashboard Token ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>Dashboard Token</p>
          <p style={SECTION_CAPTION}>
            Required for all write actions (save outcome, retry, resolve, acknowledge alert, etc.).
            Stored in your browser only — never sent to the server on read requests.
            Value is your <code style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>SECRET_KEY</code> from the backend .env.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", maxWidth: 480 }}>
          <input
            type="password"
            value={dashboardToken}
            onChange={(e) => setDashboardToken(e.target.value)}
            placeholder="Paste your dashboard secret key…"
            style={{ ...INPUT, flex: 1 }}
          />
          <button
            onClick={handleSaveToken}
            style={{
              padding: "0.4rem 1rem",
              background: "#1e293b", color: "#fff", border: "none", borderRadius: 6,
              fontSize: "0.85rem", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" as const,
            }}
          >
            Save token
          </button>
        </div>
        {dashboardToken && (
          <p style={{ margin: 0, fontSize: "0.72rem", color: "#16a34a" }}>
            Token is set in this browser.
          </p>
        )}
      </div>

      {/* ── Operator ID ── */}
      <div style={SECTION_CARD}>
        <div>
          <label style={LABEL}>Your name / operator ID</label>
          <p style={SECTION_CAPTION}>Recorded in the audit log with every save.</p>
        </div>
        <input
          type="text"
          value={operatorId}
          onChange={(e) => setOperatorId(e.target.value)}
          placeholder="e.g. alice or ops-team"
          style={{ ...INPUT, maxWidth: 320 }}
        />
      </div>

      {/* ── New Lead — Calling Window ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>New Lead — Calling Window</p>
          <p style={SECTION_CAPTION}>Active all 7 days by default. Times are in the caller&apos;s local timezone.</p>
        </div>
        <div>
          <label style={LABEL}>Active days</label>
          <DayPicker value={nlDays} onChange={setNlDays} />
        </div>
        <div style={{ display: "flex", gap: "1rem" }}>
          <div>
            <label style={LABEL}>Start hour (24-h, 0–23)</label>
            <input type="number" min={0} max={23} value={nlStart}
              onChange={(e) => setNlStart(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>End hour (24-h, 1–24)</label>
            <input type="number" min={1} max={24} value={nlEnd}
              onChange={(e) => setNlEnd(Number(e.target.value))} style={NUM_INPUT} />
          </div>
        </div>
        {nlStart >= nlEnd && (
          <p style={{ color: "#f59e0b", fontSize: "0.8rem", margin: 0 }}>
            Start hour must be less than end hour.
          </p>
        )}
      </div>

      {/* ── Cold Lead — Calling Window ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>Cold Lead — Calling Window</p>
          <p style={SECTION_CAPTION}>Mon–Fri only by default. Times are in the caller&apos;s local timezone.</p>
        </div>
        <div>
          <label style={LABEL}>Active days</label>
          <DayPicker value={clDays} onChange={setClDays} />
        </div>
        <div style={{ display: "flex", gap: "1rem" }}>
          <div>
            <label style={LABEL}>Start hour (24-h, 0–23)</label>
            <input type="number" min={0} max={23} value={clStart}
              onChange={(e) => setClStart(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>End hour (24-h, 1–24)</label>
            <input type="number" min={1} max={24} value={clEnd}
              onChange={(e) => setClEnd(Number(e.target.value))} style={NUM_INPUT} />
          </div>
        </div>
        {clStart >= clEnd && (
          <p style={{ color: "#f59e0b", fontSize: "0.8rem", margin: 0 }}>
            Start hour must be less than end hour.
          </p>
        )}
      </div>

      {/* ── Cold Lead — VM Retry Delays ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>Cold Lead — Voicemail Retry Delays</p>
          <p style={SECTION_CAPTION}>
            Delay before scheduling the next outbound call after each voicemail.
            Tier progression: None → 0 → 1 → 2 → 3 (terminal).
          </p>
        </div>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
          <div>
            <label style={LABEL}>Tier None→0 (min)</label>
            <input type="number" min={1} value={coldTNone}
              onChange={(e) => setColdTNone(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>Tier 0→1 (min)</label>
            <input type="number" min={1} value={coldT0}
              onChange={(e) => setColdT0(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>Tier 1→2 (min)</label>
            <input type="number" min={1} value={coldT1}
              onChange={(e) => setColdT1(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", paddingBottom: 2 }}>
            <input
              type="checkbox" id="cold_finalize" checked={coldFinalize}
              onChange={(e) => setColdFinalize(e.target.checked)}
              style={{ width: 16, height: 16, cursor: "pointer" }}
            />
            <label htmlFor="cold_finalize" style={{ ...LABEL, marginBottom: 0, cursor: "pointer" }}>
              Finalize at tier 2
            </label>
          </div>
        </div>
      </div>

      {/* ── New Lead — VM Retry Delays ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>New Lead — Voicemail Retry Delays</p>
          <p style={SECTION_CAPTION}>Same tier model as Cold Lead but with independent timing.</p>
        </div>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
          <div>
            <label style={LABEL}>Tier None→0 (min)</label>
            <input type="number" min={1} value={newTNone}
              onChange={(e) => setNewTNone(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>Tier 0→1 (min)</label>
            <input type="number" min={1} value={newT0}
              onChange={(e) => setNewT0(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>Tier 1→2 (min)</label>
            <input type="number" min={1} value={newT1}
              onChange={(e) => setNewT1(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", paddingBottom: 2 }}>
            <input
              type="checkbox" id="new_finalize" checked={newFinalize}
              onChange={(e) => setNewFinalize(e.target.checked)}
              style={{ width: 16, height: 16, cursor: "pointer" }}
            />
            <label htmlFor="new_finalize" style={{ ...LABEL, marginBottom: 0, cursor: "pointer" }}>
              Finalize at tier 2
            </label>
          </div>
        </div>
      </div>

      {/* ── Messaging — Follow-up Delays ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>Messaging — Follow-up Delays</p>
        </div>
        <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap" }}>
          <div>
            <label style={LABEL}>SMS follow-up delay (minutes)</label>
            <p style={SECTION_CAPTION}>How long after a missed call / voicemail before the SMS is sent.</p>
            <input type="number" min={1} value={smsDelay}
              onChange={(e) => setSmsDelay(Number(e.target.value))} style={NUM_INPUT} />
          </div>
          <div>
            <label style={LABEL}>Email follow-up delay (days)</label>
            <p style={SECTION_CAPTION}>How long after the second missed call before the email is sent.</p>
            <input type="number" min={1} value={emailDelay}
              onChange={(e) => setEmailDelay(Number(e.target.value))} style={NUM_INPUT} />
          </div>
        </div>
      </div>

      {/* ── Brand & Messaging Identity ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>Brand &amp; Messaging Identity</p>
          <p style={SECTION_CAPTION}>
            These values are injected into every AI-generated SMS and email.
            Changes take effect on the next message generation.
          </p>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.875rem" }}>
          <div>
            <label style={LABEL}>Brand name</label>
            <p style={SECTION_CAPTION}>Inserted as {"{brand_name}"} in all prompts.</p>
            <input type="text" value={brandName}
              onChange={(e) => setBrandName(e.target.value)} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Sender name</label>
            <p style={SECTION_CAPTION}>How Cora signs emails and introduces herself in SMS.</p>
            <input type="text" value={senderName}
              onChange={(e) => setSenderName(e.target.value)} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Reply-to email</label>
            <input type="email" value={replyToEmail}
              onChange={(e) => setReplyToEmail(e.target.value)} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Unsubscribe text (TCPA)</label>
            <p style={SECTION_CAPTION}>Appended to every SMS.</p>
            <input type="text" value={unsubscribeText}
              onChange={(e) => setUnsubscribeText(e.target.value)} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Next class start</label>
            <p style={SECTION_CAPTION}>e.g. &quot;May 12&quot; or &quot;Q3 2026&quot;. Used in urgency messaging.</p>
            <input type="text" value={nextClassStart}
              onChange={(e) => setNextClassStart(e.target.value)} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Live Open House RSVP link</label>
            <input type="url" value={liveOpenHouseLink}
              onChange={(e) => setLiveOpenHouseLink(e.target.value)} style={INPUT} />
          </div>
        </div>
        <div>
          <label style={LABEL}>Explainer / Open House video link</label>
          <p style={SECTION_CAPTION}>Linked in emails as a low-friction next step.</p>
          <input type="url" value={explainerVideoLink}
            onChange={(e) => setExplainerVideoLink(e.target.value)} style={INPUT} />
        </div>
      </div>

      {/* ── Admissions Assistants ── */}
      <div style={SECTION_CARD}>
        <div>
          <p style={SECTION_TITLE}>Admissions Assistants</p>
          <p style={SECTION_CAPTION}>
            GHL tasks for admissions calls are assigned to the first person in this list.
            Add or remove people here instead of editing code. Name is display-only; GHL User ID is what gets written to GHL.
          </p>
        </div>

        {admissionsAssistants.length === 0 && (
          <p style={{ margin: 0, fontSize: "0.82rem", color: "#94a3b8" }}>
            No assistants configured — fallback default will be used.
          </p>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {admissionsAssistants.map((a, i) => (
            <div key={i} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              {i === 0 && (
                <span style={{
                  fontSize: "0.68rem", fontWeight: 700, color: "#1d4ed8",
                  background: "#eff6ff", border: "1px solid #bfdbfe",
                  borderRadius: 4, padding: "0.1rem 0.4rem", whiteSpace: "nowrap" as const,
                }}>
                  PRIMARY
                </span>
              )}
              <input
                type="text"
                value={a.name}
                placeholder="Full name"
                onChange={(e) => {
                  const updated = [...admissionsAssistants];
                  updated[i] = { ...updated[i], name: e.target.value };
                  setAdmissionsAssistants(updated);
                }}
                style={{ ...INPUT, flex: 1 }}
              />
              <input
                type="text"
                value={a.ghl_id}
                placeholder="GHL User ID"
                onChange={(e) => {
                  const updated = [...admissionsAssistants];
                  updated[i] = { ...updated[i], ghl_id: e.target.value };
                  setAdmissionsAssistants(updated);
                }}
                style={{ ...INPUT, flex: 1, fontFamily: "monospace", fontSize: "0.8rem" }}
              />
              <button
                type="button"
                onClick={() => setAdmissionsAssistants(admissionsAssistants.filter((_, j) => j !== i))}
                style={{
                  padding: "0.35rem 0.7rem", background: "#fef2f2", color: "#991b1b",
                  border: "1px solid #fecaca", borderRadius: 6, fontSize: "0.8rem",
                  fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" as const,
                }}
              >
                Remove
              </button>
            </div>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setAdmissionsAssistants([...admissionsAssistants, { name: "", ghl_id: "" }])}
          style={{
            alignSelf: "flex-start",
            padding: "0.35rem 0.9rem", background: "#f0fdf4", color: "#15803d",
            border: "1px solid #bbf7d0", borderRadius: 6, fontSize: "0.82rem",
            fontWeight: 600, cursor: "pointer",
          }}
        >
          + Add assistant
        </button>
      </div>

      <div style={DIVIDER} />

      {/* ── Validation errors ── */}
      {errors.length > 0 && (
        <div style={{
          background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8,
          padding: "0.75rem 1rem", display: "flex", flexDirection: "column", gap: 4,
        }}>
          {errors.map((e) => (
            <p key={e} style={{ margin: 0, fontSize: "0.82rem", color: "#991b1b" }}>{e}</p>
          ))}
        </div>
      )}

      {/* ── Save message ── */}
      {saveMsg && (
        <div style={{
          background: saveMsg.ok ? "#f0fdf4" : "#fef2f2",
          border: `1px solid ${saveMsg.ok ? "#bbf7d0" : "#fecaca"}`,
          borderRadius: 8, padding: "0.75rem 1rem",
          color: saveMsg.ok ? "#15803d" : "#991b1b",
          fontSize: "0.85rem",
        }}>
          {saveMsg.text}
        </div>
      )}

      {/* ── Save button ── */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
        <button
          onClick={handleSave}
          disabled={saving || errors.length > 0 || !operatorId.trim()}
          style={{
            padding: "0.5rem 1.5rem",
            background: "#1e293b", color: "#fff", border: "none", borderRadius: 6,
            fontSize: "0.9rem", fontWeight: 700,
            cursor: saving || errors.length > 0 || !operatorId.trim() ? "not-allowed" : "pointer",
            opacity: saving || errors.length > 0 || !operatorId.trim() ? 0.5 : 1,
          }}
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
        {!operatorId.trim() && (
          <span style={{ fontSize: "0.78rem", color: "#94a3b8" }}>
            Enter your operator ID to enable save.
          </span>
        )}
      </div>

      {/* ── Audit trail ── */}
      <div style={{ marginTop: "0.5rem" }}>
        <button
          type="button"
          onClick={() => setShowAudit((v) => !v)}
          style={{
            background: "none", border: "none", cursor: "pointer",
            fontSize: "0.82rem", color: "#64748b", fontWeight: 600, padding: 0,
          }}
        >
          {showAudit ? "▾" : "▸"} Recent config changes (audit log)
        </button>

        {showAudit && (
          <div style={{
            marginTop: "0.5rem", background: "#fff", border: "1px solid #e2e8f0",
            borderRadius: 8, overflow: "hidden",
          }}>
            {audit.length === 0 ? (
              <p style={{ padding: "1rem", fontSize: "0.82rem", color: "#94a3b8", margin: 0 }}>
                No config changes recorded yet.
              </p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {["Timestamp (UTC)", "Operator", "Key changed"].map((h) => (
                      <th key={h} style={{
                        padding: "0.45rem 0.75rem", background: "#f8fafc",
                        borderBottom: "2px solid #e2e8f0", fontSize: "0.72rem",
                        fontWeight: 700, color: "#64748b", textTransform: "uppercase" as const,
                        letterSpacing: "0.06em", textAlign: "left",
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {audit.map((row, i) => (
                    <tr key={i} style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}>
                      <td style={{ padding: "0.4rem 0.75rem", fontSize: "0.8rem", color: "#475569", whiteSpace: "nowrap" }}>
                        {fmtTs(row.created_at)}
                      </td>
                      <td style={{ padding: "0.4rem 0.75rem", fontSize: "0.8rem", color: "#1e293b" }}>
                        {row.operator_id}
                      </td>
                      <td style={{ padding: "0.4rem 0.75rem", fontSize: "0.8rem", fontFamily: "monospace", color: "#334155" }}>
                        {row.key}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
