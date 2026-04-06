"use client";

import { useActivityFeed } from "@/lib/websocket";
import type { StreamEvent } from "@/types";

const EVENT_COLORS: Record<string, string> = {
  job_completed: "#22c55e",
  job_failed: "#ef4444",
  job_started: "#3b82f6",
  exception_created: "#f97316",
  call_processed: "#8b5cf6",
  campaign_switched: "#06b6d4",
  alert_triggered: "#eab308",
};

function EventRow({ event }: { event: StreamEvent }) {
  const color = EVENT_COLORS[event.event_type] ?? "#94a3b8";
  const ts = new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(event.created_at));

  return (
    <div
      style={{
        display: "flex",
        gap: "0.75rem",
        alignItems: "flex-start",
        padding: "0.5rem 0",
        borderBottom: "1px solid #1e293b",
      }}
    >
      <span style={{ color: "#64748b", fontSize: "0.75rem", whiteSpace: "nowrap", paddingTop: 2 }}>
        {ts}
      </span>
      <span
        style={{
          background: color + "22",
          color,
          fontSize: "0.7rem",
          padding: "1px 6px",
          borderRadius: 4,
          whiteSpace: "nowrap",
        }}
      >
        {event.event_type}
      </span>
      <span style={{ fontSize: "0.875rem", color: "#cbd5e1" }}>{event.message}</span>
      {event.contact_id && (
        <a
          href={`/lead/${event.contact_id}`}
          style={{ marginLeft: "auto", fontSize: "0.75rem", color: "#3b82f6", whiteSpace: "nowrap" }}
        >
          {event.contact_id.slice(0, 8)}…
        </a>
      )}
    </div>
  );
}

export default function ActivityFeed() {
  const { events, status } = useActivityFeed();

  const statusLabel =
    status === "live" ? "● Live" : status === "polling" ? "○ Polling" : "○ Connecting…";
  const statusColor = status === "live" ? "#22c55e" : "#94a3b8";

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem" }}>
        <span style={{ fontSize: "0.75rem", color: statusColor }}>{statusLabel}</span>
        <span style={{ fontSize: "0.75rem", color: "#475569" }}>{events.length} events</span>
      </div>
      <div style={{ maxHeight: 400, overflowY: "auto" }}>
        {events.length === 0 ? (
          <p style={{ color: "#475569", fontSize: "0.875rem" }}>No events yet.</p>
        ) : (
          events.map((e) => <EventRow key={e.id} event={e} />)
        )}
      </div>
    </div>
  );
}
