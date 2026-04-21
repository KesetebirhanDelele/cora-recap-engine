"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchMode,
  updateMode,
  pauseSystem,
  resumeSystem,
  type ModeFlags,
  type ModeResponse,
  type PreflightCheck,
  ApiError,
} from "@/lib/api";

// ── Colour tokens ─────────────────────────────────────────────────────────────
const C = {
  live:    { bg: "#dcfce7", text: "#15803d", border: "#86efac", dot: "#16a34a" },
  shadow:  { bg: "#fef9c3", text: "#854d0e", border: "#fde047", dot: "#ca8a04" },
  paused:  { bg: "#fee2e2", text: "#991b1b", border: "#fca5a5", dot: "#dc2626" },
  ok:      { bg: "#f0fdf4", text: "#15803d", border: "#86efac" },
  warning: { bg: "#fffbeb", text: "#92400e", border: "#fcd34d" },
  error:   { bg: "#fef2f2", text: "#991b1b", border: "#fca5a5" },
  card:    "#ffffff",
  border:  "#e2e8f0",
  muted:   "#64748b",
  faint:   "#f8fafc",
};

// ── Types ─────────────────────────────────────────────────────────────────────
type Phase =
  | "idle"
  | "confirming_outbound_live"
  | "confirming_ghl_live"
  | "confirming_pause"
  | "saving";

interface ConfirmDialog {
  title: string;
  body: string;
  action: () => Promise<void>;
  danger?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function operatorId(): string {
  if (typeof window === "undefined") return "dashboard";
  return (
    localStorage.getItem("operator_id") ||
    localStorage.getItem("x_operator_id") ||
    "dashboard"
  );
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

// ── Main component ────────────────────────────────────────────────────────────
export default function SystemControlsClient() {
  const [data, setData]           = useState<ModeResponse | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [phase, setPhase]         = useState<Phase>("idle");
  const [toast, setToast]         = useState<{ msg: string; ok: boolean } | null>(null);
  const [dialog, setDialog]       = useState<ConfirmDialog | null>(null);
  const [reason, setReason]       = useState("");
  const reasonRef                 = useRef<HTMLInputElement>(null);
  const pollRef                   = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await fetchMode();
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, 15_000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [load]);

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 4000);
  };

  const runUpdate = useCallback(async (flags: Record<string, string>, reasonText: string) => {
    setPhase("saving");
    try {
      await updateMode({ flags, reason: reasonText });
      await load();
      showToast("Mode flags updated", true);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      showToast(`Failed: ${msg}`, false);
    } finally {
      setPhase("idle");
      setDialog(null);
      setReason("");
    }
  }, [load]);

  const openDialog = (d: ConfirmDialog) => {
    setDialog(d);
    setTimeout(() => reasonRef.current?.focus(), 80);
  };

  if (loading) return <LoadingState />;
  if (error || !data) return <ErrorState msg={error ?? "No data"} onRetry={load} />;

  const { flags, last_changed, preflight } = data;
  const criticalErrors = preflight.filter(c => c.status === "error");
  const hasBlocker = criticalErrors.length > 0;

  // ── System-level status ────────────────────────────────────────────────────
  const systemStatus: "paused" | "shadow" | "live" = flags.system_paused
    ? "paused"
    : (flags.shadow_mode_enabled || !flags.ghl_writes_enabled)
      ? "shadow"
      : "live";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>

      {/* ── Toast ─────────────────────────────────────────────────────────── */}
      {toast && (
        <div style={{
          position: "fixed", top: 16, right: 16, zIndex: 9999,
          background: toast.ok ? "#15803d" : "#dc2626",
          color: "#fff", padding: "0.65rem 1.1rem", borderRadius: 8,
          fontSize: "0.875rem", fontWeight: 600, boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
        }}>
          {toast.ok ? "✓ " : "✗ "}{toast.msg}
        </div>
      )}

      {/* ── Confirm dialog ─────────────────────────────────────────────────── */}
      {dialog && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 9000,
          background: "rgba(0,0,0,0.45)", display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div style={{
            background: "#fff", borderRadius: 12, padding: "1.5rem", maxWidth: 460, width: "90%",
            boxShadow: "0 20px 40px rgba(0,0,0,0.2)",
          }}>
            <h3 style={{ margin: "0 0 0.5rem", fontSize: "1rem", fontWeight: 700, color: dialog.danger ? "#dc2626" : "#0f172a" }}>
              {dialog.title}
            </h3>
            <p style={{ margin: "0 0 1rem", fontSize: "0.875rem", color: "#475569", lineHeight: 1.5 }}>
              {dialog.body}
            </p>
            <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "#374151", display: "block", marginBottom: "0.35rem" }}>
              Reason (optional)
            </label>
            <input
              ref={reasonRef}
              value={reason}
              onChange={e => setReason(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && phase !== "saving") { dialog.action(); } }}
              placeholder="e.g. Phase 2 go-live"
              style={{
                width: "100%", boxSizing: "border-box",
                padding: "0.5rem 0.65rem", border: "1px solid #d1d5db",
                borderRadius: 6, fontSize: "0.875rem", marginBottom: "1rem",
                outline: "none",
              }}
            />
            <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end" }}>
              <button
                onClick={() => { setDialog(null); setReason(""); }}
                disabled={phase === "saving"}
                style={{ padding: "0.45rem 1rem", border: "1px solid #d1d5db", borderRadius: 6, background: "#fff", cursor: "pointer", fontSize: "0.875rem" }}
              >
                Cancel
              </button>
              <button
                onClick={() => dialog.action()}
                disabled={phase === "saving"}
                style={{
                  padding: "0.45rem 1rem", borderRadius: 6, border: "none",
                  background: dialog.danger ? "#dc2626" : "#2563eb",
                  color: "#fff", fontWeight: 700, cursor: phase === "saving" ? "not-allowed" : "pointer",
                  fontSize: "0.875rem", opacity: phase === "saving" ? 0.6 : 1,
                }}
              >
                {phase === "saving" ? "Applying…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── System status banner ──────────────────────────────────────────── */}
      <StatusBanner status={systemStatus} flags={flags} />

      {/* ── Pre-flight checklist ──────────────────────────────────────────── */}
      <PreflightSection checks={preflight} />

      {/* ── Outbound calls toggle ─────────────────────────────────────────── */}
      <Section title="Outbound Calls, SMS & Email" icon="📞">
        <ModeRow
          label="Outbound calls / SMS / email"
          description="Controls whether Cora places real Synthflow calls and sends SMS/email. Shadow = intercepted and logged only."
          isLive={!flags.shadow_mode_enabled}
          isPaused={flags.system_paused}
          lastChanged={last_changed["shadow_mode_enabled"]}
          onEnable={() => openDialog({
            title: "Enable outbound calls, SMS & email",
            body: "Cora will begin placing real Synthflow calls and sending SMS/email. Ensure all pre-flight checks are green before proceeding.",
            danger: false,
            action: () => runUpdate({ shadow_mode_enabled: "false" }, reason),
          })}
          onDisable={() => openDialog({
            title: "Return outbound to shadow mode",
            body: "Outbound calls, SMS, and email will be intercepted and logged. In-flight jobs will not be cancelled — they will silently shadow.",
            danger: false,
            action: () => runUpdate({ shadow_mode_enabled: "true" }, reason),
          })}
          disableEnabled={hasBlocker && flags.shadow_mode_enabled}
          disableReason={hasBlocker ? "Resolve pre-flight errors first" : undefined}
        />
      </Section>

      {/* ── GHL writes ──────────────────────────────────────────────────────── */}
      <Section title="GHL (GoHighLevel) Writes" icon="🔗">
        {/* Master toggle */}
        <ModeRow
          label="GHL Write Mode (master)"
          description="Controls all GHL field/task writes. 'Live' = real API calls to GoHighLevel. Sub-toggles below only take effect when master is live."
          isLive={flags.ghl_writes_enabled}
          isPaused={flags.system_paused}
          lastChanged={last_changed["ghl_write_mode"]}
          onEnable={() => openDialog({
            title: "Enable GHL live writes",
            body: "Cora will write contact fields, tasks, and student summaries directly to GoHighLevel. Verify all GHL field IDs are correct in .env before proceeding.",
            danger: false,
            action: () => runUpdate({ ghl_write_mode: "live", ghl_write_shadow_log_only: "false" }, reason),
          })}
          onDisable={() => openDialog({
            title: "Return GHL writes to shadow",
            body: "All GHL writes will be intercepted and logged. No data will be written to GoHighLevel.",
            danger: false,
            action: () => runUpdate({ ghl_write_mode: "shadow" }, reason),
          })}
          disableEnabled={hasBlocker && !flags.ghl_writes_enabled}
          disableReason={hasBlocker ? "Resolve pre-flight errors first" : undefined}
        />

        {/* Sub-toggles — only active when master is live */}
        <div style={{
          marginTop: "0.75rem", paddingLeft: "1.5rem",
          borderLeft: `3px solid ${flags.ghl_writes_enabled ? "#86efac" : "#e2e8f0"}`,
          display: "flex", flexDirection: "column", gap: "0.5rem",
          opacity: flags.ghl_writes_enabled ? 1 : 0.45,
        }}>
          {([
            ["ghl_write_contact_fields", "Contact Field Updates", "Updates AI Lead Classification, Mark as Lead, AI Campaign, etc."],
            ["ghl_write_tasks",           "Task Creation",          "Creates follow-up tasks in GHL after completed calls."],
            ["ghl_write_summary",         "Student Summary",        "Writes AI-generated student recap when consent is detected."],
            ["ghl_write_campaign_state",  "Campaign State",         "Updates AI Campaign field (Yes/No) to control GHL automations."],
            ["ghl_write_finalization",    "Finalization Writes",    "Writes AI Campaign = No when lead reaches terminal voicemail tier."],
          ] as [keyof ModeFlags, string, string][]).map(([key, label, desc]) => (
            <SubToggle
              key={key}
              label={label}
              description={desc}
              isOn={flags[key] as boolean}
              disabled={!flags.ghl_writes_enabled || flags.system_paused}
              lastChanged={last_changed[key]}
              onChange={(on) => {
                runUpdate({ [key]: on ? "true" : "false" }, `${label} toggled via dashboard`);
              }}
            />
          ))}
        </div>
      </Section>

      {/* ── System pause ─────────────────────────────────────────────────────── */}
      <Section title="System Pause" icon="⏸">
        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <p style={{ margin: 0, fontSize: "0.875rem", color: "#475569", lineHeight: 1.55 }}>
            Pausing the system causes all workers to hold claimed jobs without executing them.
            Incoming webhook events are still stored in Postgres. Jobs resume automatically when unpaused.
            Use this for maintenance, investigations, or before making configuration changes.
          </p>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <button
              disabled={flags.system_paused || phase === "saving"}
              onClick={() => openDialog({
                title: "Pause the system",
                body: "Workers will stop executing jobs. Call events and webhook payloads will continue to be stored. All paused jobs resume automatically when you unpause.",
                danger: true,
                action: async () => {
                  setPhase("saving");
                  try {
                    await pauseSystem();
                    await load();
                    showToast("System paused", true);
                  } catch (e) {
                    showToast(`Failed: ${e instanceof ApiError ? e.message : String(e)}`, false);
                  } finally { setPhase("idle"); setDialog(null); }
                },
              })}
              style={{
                padding: "0.5rem 1.25rem", borderRadius: 7, border: "none",
                background: flags.system_paused ? "#e2e8f0" : "#dc2626",
                color: flags.system_paused ? "#94a3b8" : "#fff",
                fontWeight: 700, fontSize: "0.875rem",
                cursor: flags.system_paused || phase === "saving" ? "not-allowed" : "pointer",
                transition: "background 0.2s",
              }}
            >
              {flags.system_paused ? "System is Paused" : "Pause System"}
            </button>

            {flags.system_paused && (
              <button
                disabled={phase === "saving"}
                onClick={async () => {
                  setPhase("saving");
                  try {
                    await resumeSystem();
                    await load();
                    showToast("System resumed", true);
                  } catch (e) {
                    showToast(`Failed: ${e instanceof ApiError ? e.message : String(e)}`, false);
                  } finally { setPhase("idle"); }
                }}
                style={{
                  padding: "0.5rem 1.25rem", borderRadius: 7, border: "none",
                  background: "#15803d", color: "#fff",
                  fontWeight: 700, fontSize: "0.875rem",
                  cursor: phase === "saving" ? "not-allowed" : "pointer",
                }}
              >
                Resume System
              </button>
            )}
          </div>

          {last_changed["system_paused"] && (
            <p style={{ margin: 0, fontSize: "0.775rem", color: "#94a3b8" }}>
              Last changed: {fmtTime(last_changed["system_paused"].at)} by {last_changed["system_paused"].operator_id}
            </p>
          )}
        </div>
      </Section>

      {/* ── Change log ──────────────────────────────────────────────────────── */}
      <ChangeLog lastChanged={last_changed} />
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusBanner({ status, flags }: { status: "paused" | "shadow" | "live"; flags: ModeFlags }) {
  const cfg = C[status];
  const labels: Record<typeof status, string> = {
    paused: "CORA IS PAUSED",
    shadow: "CORA IS IN SHADOW MODE",
    live:   "CORA IS LIVE",
  };
  const descs: Record<typeof status, string> = {
    paused: "Workers are holding jobs. No calls, SMS, email, or GHL writes are executing.",
    shadow: [
      flags.shadow_mode_enabled ? "Outbound calls/SMS/email are intercepted." : null,
      !flags.ghl_writes_enabled ? "GHL writes are shadow-logged." : null,
    ].filter(Boolean).join(" ") || "Some channels are in shadow mode.",
    live:   "All channels are live. Synthflow calls, SMS/email, and GHL writes are executing.",
  };
  return (
    <div style={{
      background: cfg.bg, border: `1.5px solid ${cfg.border}`,
      borderRadius: 10, padding: "0.85rem 1.1rem",
      display: "flex", alignItems: "center", gap: "0.75rem",
    }}>
      <span style={{
        width: 12, height: 12, borderRadius: "50%",
        background: cfg.dot, flexShrink: 0,
        boxShadow: status === "live" ? `0 0 0 4px ${cfg.bg}` : undefined,
      }} />
      <div>
        <span style={{ fontSize: "0.85rem", fontWeight: 800, color: cfg.text, letterSpacing: "0.04em" }}>
          {labels[status]}
        </span>
        <span style={{ fontSize: "0.8rem", color: cfg.text, marginLeft: "0.5rem", opacity: 0.85 }}>
          — {descs[status]}
        </span>
      </div>
    </div>
  );
}

function PreflightSection({ checks }: { checks: PreflightCheck[] }) {
  const [open, setOpen] = useState(true);
  const errors   = checks.filter(c => c.status === "error");
  const warnings = checks.filter(c => c.status === "warning");
  const oks      = checks.filter(c => c.status === "ok");

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: "100%", textAlign: "left", padding: "0.9rem 1.1rem",
          background: "none", border: "none", cursor: "pointer",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}
      >
        <span style={{ fontWeight: 700, fontSize: "0.9rem", color: "#0f172a" }}>
          Pre-flight Checklist
          {errors.length > 0 && (
            <span style={{ marginLeft: "0.5rem", fontSize: "0.75rem", background: "#fee2e2", color: "#dc2626", padding: "1px 7px", borderRadius: 20, fontWeight: 700 }}>
              {errors.length} error{errors.length > 1 ? "s" : ""}
            </span>
          )}
          {warnings.length > 0 && (
            <span style={{ marginLeft: "0.4rem", fontSize: "0.75rem", background: "#fef9c3", color: "#854d0e", padding: "1px 7px", borderRadius: 20, fontWeight: 600 }}>
              {warnings.length} warning{warnings.length > 1 ? "s" : ""}
            </span>
          )}
          {errors.length === 0 && warnings.length === 0 && (
            <span style={{ marginLeft: "0.5rem", fontSize: "0.75rem", background: "#dcfce7", color: "#15803d", padding: "1px 7px", borderRadius: 20, fontWeight: 700 }}>
              All clear
            </span>
          )}
        </span>
        <span style={{ fontSize: "0.8rem", color: "#94a3b8" }}>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={{ borderTop: `1px solid ${C.border}` }}>
          {[...errors, ...warnings, ...oks].map(c => {
            const cfg = C[c.status];
            return (
              <div key={c.key} style={{
                display: "flex", gap: "0.65rem", alignItems: "flex-start",
                padding: "0.5rem 1.1rem", borderBottom: `1px solid #f1f5f9`,
              }}>
                <span style={{
                  marginTop: 2, fontSize: "0.7rem", fontWeight: 700,
                  background: cfg.bg, color: cfg.text, padding: "1px 6px",
                  borderRadius: 4, flexShrink: 0, minWidth: 48, textAlign: "center",
                }}>
                  {c.status.toUpperCase()}
                </span>
                <div>
                  <div style={{ fontSize: "0.825rem", fontWeight: 600, color: "#1e293b" }}>{c.label}</div>
                  <div style={{ fontSize: "0.775rem", color: "#64748b" }}>{c.detail}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "1rem 1.1rem" }}>
      <h2 style={{ margin: "0 0 0.85rem", fontSize: "0.925rem", fontWeight: 700, color: "#0f172a", display: "flex", alignItems: "center", gap: "0.4rem" }}>
        <span>{icon}</span>{title}
      </h2>
      {children}
    </div>
  );
}

interface ModeRowProps {
  label: string;
  description: string;
  isLive: boolean;
  isPaused: boolean;
  lastChanged?: { at: string | null; operator_id: string; new_value: string };
  onEnable: () => void;
  onDisable: () => void;
  disableEnabled?: boolean;
  disableReason?: string;
}

function ModeRow({ label, description, isLive, isPaused, lastChanged, onEnable, onDisable, disableEnabled, disableReason }: ModeRowProps) {
  const effectiveIsLive = isLive && !isPaused;
  const colorsOn  = C.live;
  const colorsOff = C.shadow;
  const active    = effectiveIsLive ? colorsOn : colorsOff;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "#1e293b" }}>{label}</div>
        <div style={{ fontSize: "0.775rem", color: "#64748b", marginTop: 2 }}>{description}</div>
        {lastChanged && (
          <div style={{ fontSize: "0.72rem", color: "#94a3b8", marginTop: 3 }}>
            Last changed: {fmtTime(lastChanged.at)} by {lastChanged.operator_id}
          </div>
        )}
      </div>

      <div style={{
        display: "flex", alignItems: "center", gap: "0.65rem",
        background: active.bg, border: `1px solid ${active.border}`,
        borderRadius: 8, padding: "0.35rem 0.75rem",
      }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: active.dot, flexShrink: 0 }} />
        <span style={{ fontSize: "0.8rem", fontWeight: 700, color: active.text, minWidth: 55 }}>
          {isPaused ? "PAUSED" : isLive ? "LIVE" : "SHADOW"}
        </span>
        {!isPaused && (
          <button
            title={disableEnabled ? disableReason : undefined}
            onClick={isLive ? onDisable : onEnable}
            disabled={!!disableEnabled}
            style={{
              padding: "0.3rem 0.75rem", border: "none", borderRadius: 5,
              background: isLive ? "#f1f5f9" : "#2563eb",
              color: isLive ? "#475569" : "#fff",
              fontSize: "0.775rem", fontWeight: 600,
              cursor: disableEnabled ? "not-allowed" : "pointer",
              opacity: disableEnabled ? 0.5 : 1,
            }}
          >
            {isLive ? "← Shadow" : "Go Live →"}
          </button>
        )}
      </div>
    </div>
  );
}

function SubToggle({
  label, description, isOn, disabled, lastChanged, onChange,
}: {
  label: string; description: string; isOn: boolean; disabled: boolean;
  lastChanged?: { at: string | null; operator_id: string };
  onChange: (on: boolean) => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: "0.825rem", fontWeight: 600, color: "#1e293b" }}>{label}</div>
        <div style={{ fontSize: "0.75rem", color: "#64748b" }}>{description}</div>
        {lastChanged?.at && (
          <div style={{ fontSize: "0.7rem", color: "#94a3b8" }}>
            Changed: {fmtTime(lastChanged.at)} by {lastChanged.operator_id}
          </div>
        )}
      </div>

      {/* Toggle pill */}
      <button
        disabled={disabled}
        onClick={() => onChange(!isOn)}
        style={{
          position: "relative", width: 44, height: 24, border: "none",
          borderRadius: 12,
          background: isOn ? "#16a34a" : "#cbd5e1",
          cursor: disabled ? "not-allowed" : "pointer",
          transition: "background 0.2s",
          flexShrink: 0,
          padding: 0,
        }}
        title={disabled ? "Enable GHL live writes first" : undefined}
      >
        <span style={{
          position: "absolute", top: 3,
          left: isOn ? 23 : 3,
          width: 18, height: 18, borderRadius: "50%",
          background: "#fff", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
          transition: "left 0.15s",
        }} />
      </button>

      <span style={{
        fontSize: "0.75rem", fontWeight: 600,
        color: isOn ? "#15803d" : "#94a3b8",
        minWidth: 28,
      }}>
        {isOn ? "On" : "Off"}
      </span>
    </div>
  );
}

function ChangeLog({ lastChanged }: { lastChanged: Record<string, { at: string | null; operator_id: string; new_value: string }> }) {
  const entries = Object.entries(lastChanged)
    .filter(([, v]) => v.at)
    .sort((a, b) => (b[1].at ?? "").localeCompare(a[1].at ?? ""))
    .slice(0, 10);

  if (entries.length === 0) return null;

  const labelMap: Record<string, string> = {
    shadow_mode_enabled:       "Outbound Calls/SMS/Email",
    ghl_write_mode:            "GHL Write Mode",
    ghl_write_shadow_log_only: "GHL Shadow Log Only",
    ghl_write_contact_fields:  "Contact Field Updates",
    ghl_write_tasks:           "Task Creation",
    ghl_write_summary:         "Student Summary",
    ghl_write_campaign_state:  "Campaign State",
    ghl_write_finalization:    "Finalization Writes",
    system_paused:             "System Pause",
  };

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "1rem 1.1rem" }}>
      <h2 style={{ margin: "0 0 0.75rem", fontSize: "0.925rem", fontWeight: 700, color: "#0f172a" }}>
        📋 Mode Change Log
      </h2>
      <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {entries.map(([key, val]) => (
          <div key={key} style={{
            display: "flex", gap: "0.75rem", alignItems: "center",
            padding: "0.4rem 0", borderBottom: "1px solid #f1f5f9", fontSize: "0.8rem",
          }}>
            <span style={{ color: "#94a3b8", minWidth: 130, fontSize: "0.75rem" }}>{fmtTime(val.at)}</span>
            <span style={{ fontWeight: 600, color: "#334155", flex: 1 }}>{labelMap[key] ?? key}</span>
            <span style={{
              padding: "1px 8px", borderRadius: 12, fontWeight: 700, fontSize: "0.72rem",
              background: val.new_value === "true" || val.new_value === "live" ? "#dcfce7" : "#fef9c3",
              color:      val.new_value === "true" || val.new_value === "live" ? "#15803d" : "#854d0e",
            }}>
              {val.new_value}
            </span>
            <span style={{ color: "#94a3b8", fontSize: "0.72rem" }}>by {val.operator_id}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div style={{ padding: "2rem", textAlign: "center", color: "#94a3b8", fontSize: "0.875rem" }}>
      Loading system controls…
    </div>
  );
}

function ErrorState({ msg, onRetry }: { msg: string; onRetry: () => void }) {
  return (
    <div style={{ padding: "2rem", textAlign: "center" }}>
      <p style={{ color: "#dc2626", fontSize: "0.875rem", marginBottom: "1rem" }}>
        Failed to load mode flags: {msg}
      </p>
      <button onClick={onRetry} style={{ padding: "0.4rem 1rem", borderRadius: 6, border: "1px solid #d1d5db", cursor: "pointer" }}>
        Retry
      </button>
    </div>
  );
}
