"use client";

import Link from "next/link";
import type { HealthResponse } from "@/types";

interface Props {
  health: HealthResponse;
}

// ── Design tokens ─────────────────────────────────────────────────────────────
const C = {
  cardBg:     "#ffffff",
  border:     "#e2e8f0",
  muted:      "#64748b",
  sub:        "#94a3b8",

  critBg:     "#fef2f2",
  critBorder: "#fecaca",
  critText:   "#dc2626",
  critGlow:   "0 0 0 2px #fecaca",
  critAccent: "#ef4444",

  warnBg:     "#fffbeb",
  warnBorder: "#fde68a",
  warnText:   "#d97706",
  warnGlow:   "0 0 0 2px #fde68a",
  warnAccent: "#f59e0b",

  okText:     "#0f172a",
  okAccent:   "#e2e8f0",
};

function st(critical?: boolean, warn?: boolean) {
  if (critical) return { bg: C.critBg, border: C.critBorder, text: C.critText, glow: C.critGlow, accent: C.critAccent };
  if (warn)     return { bg: C.warnBg, border: C.warnBorder, text: C.warnText, glow: C.warnGlow, accent: C.warnAccent };
  return        { bg: C.cardBg, border: C.border, text: C.okText, glow: "none", accent: C.okAccent };
}

// ── Primary tile ──────────────────────────────────────────────────────────────
function PrimaryTile({
  label, value, sublabel, critical, warn, href, icon,
}: {
  label: string; value: string | number; sublabel?: string;
  critical?: boolean; warn?: boolean; href?: string; icon?: string;
}) {
  const s = st(critical, warn);

  const tile = (
    <div
      style={{
        flex: 1,
        background: s.bg,
        border: `1px solid ${s.border}`,
        borderTop: `2px solid ${s.accent}`,
        borderRadius: 10,
        padding: "0.75rem 1rem",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        boxShadow: s.glow !== "none" ? s.glow : "0 1px 3px rgba(0,0,0,0.06)",
        cursor: href ? "pointer" : "default",
        transition: "transform 0.15s ease, box-shadow 0.15s ease",
        minWidth: 0,
      }}
      onMouseEnter={(e) => {
        if (!href) return;
        const el = e.currentTarget as HTMLDivElement;
        el.style.transform = "translateY(-2px)";
        el.style.boxShadow = `0 6px 16px rgba(0,0,0,0.1)`;
      }}
      onMouseLeave={(e) => {
        if (!href) return;
        const el = e.currentTarget as HTMLDivElement;
        el.style.transform = "translateY(0)";
        el.style.boxShadow = s.glow !== "none" ? s.glow : "0 1px 3px rgba(0,0,0,0.06)";
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: "0.72rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 600 }}>
          {label}
        </span>
        {icon && <span style={{ fontSize: "0.9rem", opacity: 0.6 }}>{icon}</span>}
      </div>
      <div style={{ fontSize: "2rem", fontWeight: 800, color: s.text, letterSpacing: "-0.04em", lineHeight: 1 }}>
        {value}
      </div>
      {sublabel && (
        <div style={{ fontSize: "0.68rem", color: C.sub, marginTop: 4 }}>{sublabel}</div>
      )}
    </div>
  );

  return href
    ? <Link href={href} style={{ textDecoration: "none", flex: 1, display: "flex" }}>{tile}</Link>
    : tile;
}

// ── Secondary chip ────────────────────────────────────────────────────────────
function SecondaryTile({
  label, value, critical, warn,
}: {
  label: string; value: string | number; critical?: boolean; warn?: boolean;
}) {
  const s = st(critical, warn);
  return (
    <div
      style={{
        flex: 1,
        background: s.bg,
        border: `1px solid ${s.border}`,
        borderRadius: 8,
        padding: "0.5rem 0.75rem",
        minWidth: 90,
        boxShadow: (critical || warn) ? s.glow : "0 1px 2px rgba(0,0,0,0.04)",
      }}
    >
      <div style={{ fontSize: "0.68rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.2rem", fontWeight: 700, color: s.text, letterSpacing: "-0.02em" }}>
        {value}
      </div>
    </div>
  );
}

// ── Config pill ───────────────────────────────────────────────────────────────
function ConfigPill({ label, value }: { label: string; value: string }) {
  const shadow = value.toLowerCase().includes("shadow");
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 4 }}>
      <span style={{ fontSize: "0.66rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.07em" }}>{label}</span>
      <span
        style={{
          fontSize: "0.72rem",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: shadow ? "#d97706" : "#16a34a",
          background: shadow ? "#fffbeb" : "#f0fdf4",
          border: `1px solid ${shadow ? "#fde68a" : "#bbf7d0"}`,
          borderRadius: 5,
          padding: "2px 10px",
        }}
      >
        {value}
      </span>
    </div>
  );
}

// ── System status summary ─────────────────────────────────────────────────────
function StatusDot({ critical, warn }: { critical: boolean; warn: boolean }) {
  const color = critical ? "#dc2626" : warn ? "#d97706" : "#16a34a";
  const label = critical ? "Critical" : warn ? "Degraded" : "Healthy";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 7, height: 7, borderRadius: "50%",
          background: color,
          display: "inline-block",
          flexShrink: 0,
        }}
      />
      <span style={{ fontSize: "0.7rem", color, fontWeight: 600 }}>{label}</span>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────
export default function HealthTiles({ health }: Props) {
  const lag       = health.queue_lag_seconds;
  const errorRate = health.error_rate;

  const isCritical = lag > 300 || health.open_exception_count >= 10 || (errorRate !== null && errorRate > 0.2);
  const isWarn     = !isCritical && (
    lag > 60 ||
    health.open_exception_count > 0 ||
    health.stuck_job_count > 0 ||
    health.expired_lease_count > 0 ||
    health.jobs_failed_last_5m > 0 ||
    (errorRate !== null && errorRate > 0.05)
  );

  return (
    <div>
      {/* Section header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.625rem" }}>
        <span style={{ fontSize: "0.72rem", fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>
          System Health
        </span>
        <StatusDot critical={isCritical} warn={isWarn} />
      </div>

      {/* Primary row */}
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <PrimaryTile label="Queue Lag"        value={`${lag.toFixed(0)}s`}             icon="⏱" critical={lag > 300} warn={lag > 60} />
        <PrimaryTile label="Exceptions"  value={health.open_exception_count} sublabel={`Today: ${health.today_exception_count ?? 0}`} icon="⚠️" critical={health.open_exception_count >= 10} warn={health.open_exception_count > 0} href="/exceptions" />
        <PrimaryTile label="Jobs In-Flight"   value={health.active_workers}            icon="⚡" sublabel="0 = idle, not offline" critical={health.active_workers === 0 && lag > 60} />
      </div>

      {/* Secondary row + config */}
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "stretch" }}>
        <SecondaryTile label="Completed (5m)" value={health.jobs_completed_last_5m} />
        <SecondaryTile label="Failed (5m)"    value={health.jobs_failed_last_5m}   warn={health.jobs_failed_last_5m > 0} />
        <SecondaryTile label="Error Rate"     value={errorRate !== null ? `${(errorRate * 100).toFixed(1)}%` : "—"} critical={errorRate !== null && errorRate > 0.2} warn={errorRate !== null && errorRate > 0.05} />
        <SecondaryTile label="Stuck Jobs"     value={health.stuck_job_count}       warn={health.stuck_job_count > 0} />
        <SecondaryTile label="Expired Leases" value={health.expired_lease_count}   warn={health.expired_lease_count > 0} />
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "1.25rem", padding: "0.5rem 0.875rem", background: C.cardBg, border: `1px solid ${C.border}`, borderRadius: 8, boxShadow: "0 1px 2px rgba(0,0,0,0.04)" }}>
          <ConfigPill label="Mode"      value={health.shadow_mode_enabled ? "Shadow" : "Live"} />
          <div style={{ width: 1, height: 28, background: C.border }} />
          <ConfigPill label="GHL Write" value={health.ghl_write_mode} />
        </div>
      </div>
    </div>
  );
}
