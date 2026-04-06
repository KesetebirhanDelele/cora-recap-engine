"use client";

/**
 * SystemStatusBar — replaces the System Health block on the home page.
 *
 * Option A: synthesised status strip (OK / WARNING / CRITICAL) — no raw numbers.
 * Option B: active alerts inline, one row each — additive, not duplicative.
 *
 * Does NOT repeat any metric that lives on a detail page.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchAlerts } from "@/lib/api";
import type { Alert, HealthResponse } from "@/types";

interface Props {
  health: HealthResponse | null;
  healthError: string | null;
}

type Status = "critical" | "warn" | "ok" | "unknown";

const STATUS_META: Record<Status, { label: string; color: string; dot: string; bg: string; border: string }> = {
  critical: { label: "Critical",  color: "#dc2626", dot: "#ef4444", bg: "#fef2f2", border: "#fecaca" },
  warn:     { label: "Warning",   color: "#d97706", dot: "#f59e0b", bg: "#fffbeb", border: "#fde68a" },
  ok:       { label: "Healthy",   color: "#16a34a", dot: "#22c55e", bg: "#f0fdf4", border: "#bbf7d0" },
  unknown:  { label: "Unknown",   color: "#64748b", dot: "#94a3b8", bg: "#f8fafc", border: "#e2e8f0" },
};

function deriveStatus(h: HealthResponse): Status {
  const lag = h.queue_lag_seconds;
  const rate = h.error_rate;
  if (lag > 300 || h.open_exception_count >= 10 || (rate !== null && rate > 0.2)) return "critical";
  if (lag > 60 || h.open_exception_count > 0 || h.stuck_job_count > 0 ||
      h.expired_lease_count > 0 || h.jobs_failed_last_5m > 0 || (rate !== null && rate > 0.05)) return "warn";
  return "ok";
}

const DIV: React.CSSProperties = { color: "#cbd5e1" };

export default function SystemStatusBar({ health, healthError }: Props) {
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    fetchAlerts("active")
      .then((res) => setAlerts(res.alerts.filter((a) => a.status === "active")))
      .catch(() => {});
  }, []);

  const status: Status = health ? deriveStatus(health) : "unknown";
  const s = STATUS_META[status];

  const recordedAt = health?.recorded_at
    ? new Date(health.recorded_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  const shadow = health?.shadow_mode_enabled;
  const hasCriticalAlert = alerts.some((a) => a.severity === "critical");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>

      {/* ── Option A: Status strip ─────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.75rem",
          padding: "0.45rem 1rem",
          background: s.bg,
          border: `1px solid ${s.border}`,
          borderRadius: 8,
          fontSize: "0.78rem",
        }}
      >
        {/* Status dot + label */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.35rem", flexShrink: 0 }}>
          <span
            style={{
              width: 7, height: 7, borderRadius: "50%",
              background: s.dot, display: "inline-block", flexShrink: 0,
            }}
          />
          <span style={{ fontWeight: 700, color: s.color, letterSpacing: "0.02em" }}>
            {s.label}
          </span>
        </div>

        <span style={DIV}>|</span>

        {/* Last updated */}
        {recordedAt && (
          <>
            <span style={{ color: "#64748b" }}>Updated {recordedAt}</span>
            <span style={DIV}>|</span>
          </>
        )}

        {healthError && (
          <>
            <span style={{ color: "#dc2626", fontSize: "0.72rem" }}>⚠ Health data unavailable</span>
            <span style={DIV}>|</span>
          </>
        )}

        {/* Mode pill */}
        {health && (
          <span
            style={{
              fontSize: "0.7rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.07em",
              color: shadow ? "#d97706" : "#16a34a",
              background: shadow ? "#fffbeb" : "#f0fdf4",
              border: `1px solid ${shadow ? "#fde68a" : "#bbf7d0"}`,
              borderRadius: 5,
              padding: "2px 8px",
            }}
          >
            {shadow ? "◉ Shadow" : "◉ Live"}
          </span>
        )}

        {/* GHL write mode */}
        {health?.ghl_write_mode && (
          <>
            <span style={DIV}>|</span>
            <span style={{ color: "#64748b" }}>
              GHL:{" "}
              <span style={{ fontWeight: 600, color: "#1e293b", textTransform: "capitalize" }}>
                {health.ghl_write_mode}
              </span>
            </span>
          </>
        )}

        {/* Active alert count — links to Alerts page */}
        {alerts.length > 0 && (
          <Link
            href="/alerts"
            style={{ marginLeft: "auto", textDecoration: "none", flexShrink: 0, display: "flex", alignItems: "center", gap: "0.35rem" }}
          >
            <span style={{ color: hasCriticalAlert ? "#dc2626" : "#d97706", fontWeight: 700, fontSize: "0.75rem" }}>
              {hasCriticalAlert ? "🚨" : "⚠️"} {alerts.length} active alert{alerts.length !== 1 ? "s" : ""}
            </span>
            <span style={{ color: "#94a3b8", fontSize: "0.72rem" }}>→</span>
          </Link>
        )}
      </div>

      {/* ── Option B: Alert rows ───────────────────────────────────────────── */}
      {alerts.map((alert) => {
        const crit = alert.severity === "critical";
        return (
          <div
            key={alert.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.625rem",
              padding: "0.35rem 1rem",
              borderRadius: 7,
              background: crit ? "#fef2f2" : "#fffbeb",
              border: `1px solid ${crit ? "#fecaca" : "#fde68a"}`,
              borderLeft: `3px solid ${crit ? "#ef4444" : "#f97316"}`,
              fontSize: "0.78rem",
            }}
          >
            <span style={{ flexShrink: 0 }}>{crit ? "🚨" : "⚠️"}</span>
            <span
              style={{
                fontSize: "0.62rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                color: crit ? "#dc2626" : "#d97706",
                flexShrink: 0,
              }}
            >
              {alert.severity}
            </span>
            <span style={{ color: "#374151", flex: 1, minWidth: 0 }}>{alert.message}</span>
            <span style={{ fontSize: "0.62rem", color: "#94a3b8", flexShrink: 0, fontFamily: "monospace" }}>
              {alert.alert_type}
            </span>
          </div>
        );
      })}

    </div>
  );
}
