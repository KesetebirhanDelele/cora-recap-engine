/**
 * WebSocket client with automatic polling fallback.
 *
 * Connects to WS_URL/dashboard/ws/events (Redis Pub/Sub bridge).
 * On Redis failure or disconnect, switches to 5-second polling of
 * GET /dashboard/events to ensure the activity feed never goes dark.
 *
 * Usage:
 *   const feed = createActivityFeed(onEvent, onStatusChange);
 *   feed.start();
 *   // later:
 *   feed.stop();
 *
 * Or use the React hook:
 *   const { events, status } = useActivityFeed();
 */

import type { StreamEvent, WebSocketMessage } from "@/types";
import { fetchEvents } from "./api";

export type FeedStatus = "connecting" | "live" | "polling" | "error";

export interface ActivityFeed {
  start: () => void;
  stop: () => void;
}

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8001";
const POLL_INTERVAL_MS = 5_000;

export function createActivityFeed(
  onEvent: (event: StreamEvent) => void,
  onStatusChange: (status: FeedStatus) => void
): ActivityFeed {
  let ws: WebSocket | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let lastCursor: string | null = null;
  let stopped = false;

  function startPolling() {
    if (pollTimer !== null) return;
    onStatusChange("polling");
    pollTimer = setInterval(async () => {
      if (stopped) return;
      try {
        const res = await fetchEvents({ since: lastCursor ?? undefined, limit: 50 });
        for (const evt of res.events) {
          onEvent(evt);
        }
        if (res.next_cursor) lastCursor = res.next_cursor;
      } catch {
        // polling failure is non-fatal; will retry on next tick
      }
    }, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function connect() {
    if (stopped) return;
    onStatusChange("connecting");

    ws = new WebSocket(`${WS_URL}/dashboard/ws/events`);

    ws.onopen = () => {
      onStatusChange("live");
      stopPolling();
    };

    ws.onmessage = (event) => {
      try {
        const msg: WebSocketMessage = JSON.parse(event.data as string);
        if ("type" in msg && msg.type === "error") {
          // Server signalled Redis is unavailable — fall back to polling
          ws?.close();
          startPolling();
          return;
        }
        onEvent(msg as StreamEvent);
        lastCursor = (msg as StreamEvent).created_at ?? lastCursor;
      } catch {
        // malformed message; ignore
      }
    };

    ws.onerror = () => {
      onStatusChange("error");
    };

    ws.onclose = () => {
      ws = null;
      if (!stopped) {
        // Connection closed unexpectedly — fall back to polling
        startPolling();
      }
    };
  }

  return {
    start() {
      stopped = false;
      connect();
    },
    stop() {
      stopped = true;
      ws?.close();
      stopPolling();
    },
  };
}

// ── React hook ────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useRef, useState } from "react";

const MAX_FEED_EVENTS = 200;

export function useActivityFeed() {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [status, setStatus] = useState<FeedStatus>("connecting");
  const feedRef = useRef<ActivityFeed | null>(null);

  const onEvent = useCallback((event: StreamEvent) => {
    setEvents((prev) => {
      const next = [event, ...prev];
      return next.length > MAX_FEED_EVENTS ? next.slice(0, MAX_FEED_EVENTS) : next;
    });
  }, []);

  useEffect(() => {
    const feed = createActivityFeed(onEvent, setStatus);
    feedRef.current = feed;
    feed.start();
    return () => feed.stop();
  }, [onEvent]);

  return { events, status };
}
