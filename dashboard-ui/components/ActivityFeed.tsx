"use client";

import { useActivityFeed } from "@/lib/websocket";
import type { StreamEvent } from "@/types";

const EVENT_COLORS: Record<string, string> = {
  job_completed:    "#16a34a",
  job_failed:       "#dc2626",
  job_started:      "#2563eb",
  exception_created:"#ea580c",
  call_processed:   "#7c3aed",
  campaign_switched:"#0891b2",
  alert_triggered:  "#d97706",
};

function EventRow({ event }: { event: StreamEvent }) {
  const color = EVENT_COLORS[event.event_type] ?? "#64748b";
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
        borderBottom: "1px solid #f1f5f9",
      }}
    >
      <span style={{ color: "#94a3b8", fontSize: "0.75rem", whiteSpace: "nowrap", paddingTop: 2 }}>
        {ts}
      </span>
      <span
        style={{
          background: `${color}12`,
          color,
          fontSize: "0.7rem",
          fontWeight: 600,
          padding: "1px 6px",
          borderRadius: 4,
          whiteSpace: "nowrap",
          border: `1px solid ${color}25`,
        }}
      >
        {event.event_type}
      </span>
      <span style={{ fontSize: "0.875rem", color: "#374151" }}>{event.message}</span>
      {event.contact_id && (
        <a
          href={`/lead/${event.contact_id}`}
          style={{ marginLeft: "auto", fontSize: "0.75rem", color: "#2563eb", whiteSpace: "nowrap", textDecoration: "none" }}
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
  const statusColor = status === "live" ? "#16a34a" : "#94a3b8";

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem" }}>
        <span style={{ fontSize: "0.75rem", color: statusColor, fontWeight: 600 }}>{statusLabel}</span>
        <span style={{ fontSize: "0.75rem", color: "#94a3b8" }}>{events.length} events</span>
      </div>
      <div style={{ maxHeight: 400, overflowY: "auto" }}>
        {events.length === 0 ? (
          <p style={{ color: "#94a3b8", fontSize: "0.875rem" }}>No events yet.</p>
        ) : (
          events.map((e) => <EventRow key={e.id} event={e} />)
        )}
      </div>
    </div>
  );
}
