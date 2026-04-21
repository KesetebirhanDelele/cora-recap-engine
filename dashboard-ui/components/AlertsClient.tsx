"use client";

import { useEffect, useState } from "react";
import { fetchAlerts, acknowledgeAlert } from "@/lib/api";
import type { Alert, AlertStatus } from "@/types";

const SEV_COLOR: Record<string, string> = {
  critical: "#dc2626",
  warning:  "#d97706",
};

const STATUS_COLOR: Record<string, string> = {
  active:       "#dc2626",
  resolved:     "#16a34a",
  acknowledged: "#2563eb",
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function AlertRow({ alert, onAcknowledged }: { alert: Alert; onAcknowledged: (id: string) => void }) {
  const crit = alert.severity === "critical";
  const sevColor = SEV_COLOR[alert.severity] ?? "#64748b";
  const statusColor = STATUS_COLOR[alert.status] ?? "#64748b";
  const [acking, setAcking] = useState(false);
  const [ackError, setAckError] = useState<string | null>(null);

  async function handleAcknowledge() {
    setAcking(true);
    setAckError(null);
    try {
      await acknowledgeAlert(alert.id);
      onAcknowledged(alert.id);
    } catch (e) {
      setAckError(String(e));
    } finally {
      setAcking(false);
    }
  }

  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderLeft: `3px solid ${sevColor}`,
        borderRadius: 8,
        padding: "0.75rem 1rem",
        display: "flex",
        alignItems: "flex-start",
        gap: "0.875rem",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
      }}
    >
      <span style={{ fontSize: "1.1rem", flexShrink: 0, paddingTop: 1 }}>
        {crit ? "🚨" : "⚠️"}
      </span>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: 4, flexWrap: "wrap" as const }}>
          <span
            style={{
              fontSize: "0.62rem", fontWeight: 700, textTransform: "uppercase" as const,
              letterSpacing: "0.07em", color: sevColor,
              background: `${sevColor}12`, border: `1px solid ${sevColor}30`,
              borderRadius: 4, padding: "1px 6px",
            }}
          >
            {alert.severity}
          </span>
          <span
            style={{
              fontSize: "0.62rem", fontWeight: 700, textTransform: "uppercase" as const,
              letterSpacing: "0.07em", color: statusColor,
              background: `${statusColor}12`, border: `1px solid ${statusColor}30`,
              borderRadius: 4, padding: "1px 6px",
            }}
          >
            {alert.status}
          </span>
          <code style={{ fontSize: "0.72rem", color: "#64748b", fontFamily: "monospace" }}>
            {alert.alert_type}
          </code>
          <span style={{ marginLeft: "auto", fontSize: "0.68rem", color: "#94a3b8", whiteSpace: "nowrap" as const }}>
            {timeAgo(alert.created_at)}
          </span>
        </div>
        <div style={{ fontSize: "0.82rem", color: "#374151" }}>{alert.message}</div>
        {alert.current_value !== null && (
          <div style={{ marginTop: 4, fontSize: "0.7rem", color: "#64748b" }}>
            Value: <strong style={{ color: sevColor }}>{alert.current_value}</strong>
            {alert.threshold !== null && <> · Threshold: {alert.threshold}</>}
          </div>
        )}
        {ackError && (
          <div style={{ marginTop: 4, fontSize: "0.72rem", color: "#dc2626" }}>{ackError}</div>
        )}
      </div>

      {alert.status === "active" && (
        <button
          onClick={handleAcknowledge}
          disabled={acking}
          style={{
            flexShrink: 0,
            padding: "0.25rem 0.65rem",
            border: "1px solid #bfdbfe",
            borderRadius: 5,
            background: "#eff6ff",
            color: "#1d4ed8",
            fontSize: "0.72rem",
            fontWeight: 600,
            cursor: acking ? "not-allowed" : "pointer",
            opacity: acking ? 0.6 : 1,
            whiteSpace: "nowrap" as const,
          }}
        >
          {acking ? "…" : "Acknowledge"}
        </button>
      )}
    </div>
  );
}

const TABS: AlertStatus[] = ["active", "resolved", "acknowledged"];

export default function AlertsClient() {
  const [tab, setTab] = useState<AlertStatus>("active");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchAlerts(tab)
      .then((res) => { if (!cancelled) setAlerts(res.alerts); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tab]);

  function handleAcknowledged(id: string) {
    // Optimistically remove from active list; user can switch to Acknowledged tab to see it
    setAlerts((prev) => prev.filter((a) => a.id !== id));
  }

  return (
    <div>
      {/* Tab bar */}
      <div style={{ display: "flex", gap: "0.375rem", marginBottom: "1rem" }}>
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "0.3rem 0.875rem",
              borderRadius: 6,
              border: "1px solid",
              fontSize: "0.78rem",
              fontWeight: 600,
              cursor: "pointer",
              background: tab === t ? "#1e293b" : "#ffffff",
              color: tab === t ? "#f8fafc" : "#64748b",
              borderColor: tab === t ? "#1e293b" : "#e2e8f0",
              textTransform: "capitalize" as const,
              transition: "all 0.15s",
            }}
          >
            {t}
          </button>
        ))}
        {!loading && (
          <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "#94a3b8", alignSelf: "center" }}>
            {alerts.length} alert{alerts.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {loading && <p style={{ color: "#64748b", fontSize: "0.875rem" }}>Loading…</p>}
      {error   && <p style={{ color: "#dc2626", fontSize: "0.875rem" }}>Error: {error}</p>}
      {!loading && !error && alerts.length === 0 && (
        <div
          style={{
            background: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            padding: "2rem",
            textAlign: "center" as const,
            color: "#94a3b8",
            fontSize: "0.875rem",
          }}
        >
          No {tab} alerts
        </div>
      )}
      {!loading && !error && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {alerts.map((a) => <AlertRow key={a.id} alert={a} onAcknowledged={handleAcknowledged} />)}
        </div>
      )}
    </div>
  );
}
