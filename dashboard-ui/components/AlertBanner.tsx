"use client";

import type { Alert } from "@/types";

interface Props {
  alerts?: Alert[];
}

export default function AlertBanner({ alerts = [] }: Props) {
  const active = alerts.filter((a) => a.status === "active");
  if (active.length === 0) return null;

  return (
    <div style={{ marginBottom: "1rem" }}>
      {active.map((alert) => (
        <div
          key={alert.id}
          style={{
            background: alert.severity === "critical" ? "#7f1d1d" : "#78350f",
            border: `1px solid ${alert.severity === "critical" ? "#ef4444" : "#f97316"}`,
            borderRadius: 6,
            padding: "0.75rem 1rem",
            marginBottom: "0.5rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <span
              style={{
                fontSize: "0.7rem",
                fontWeight: "bold",
                textTransform: "uppercase",
                color: alert.severity === "critical" ? "#fca5a5" : "#fdba74",
                marginRight: 8,
              }}
            >
              {alert.severity}
            </span>
            <span style={{ fontSize: "0.875rem" }}>{alert.message}</span>
          </div>
          <span style={{ fontSize: "0.7rem", color: "#94a3b8" }}>
            {alert.alert_type}
          </span>
        </div>
      ))}
    </div>
  );
}
