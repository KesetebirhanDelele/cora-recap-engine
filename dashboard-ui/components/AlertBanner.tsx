"use client";

import { useEffect, useState } from "react";
import { fetchAlerts } from "@/lib/api";
import type { Alert } from "@/types";

/**
 * AlertBanner — self-fetching. Renders nothing (zero height) when no active alerts.
 */
export default function AlertBanner() {
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    fetchAlerts("active")
      .then((res) => setAlerts(res.alerts.filter((a) => a.status === "active")))
      .catch(() => {});
  }, []);

  if (alerts.length === 0) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem", padding: "0.5rem 0 0" }}>
      {alerts.map((alert) => {
        const crit = alert.severity === "critical";
        return (
          <div
            key={alert.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.625rem",
              padding: "0.4rem 0.875rem",
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
                textTransform: "uppercase" as const,
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
