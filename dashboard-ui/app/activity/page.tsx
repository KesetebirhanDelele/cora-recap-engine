import ActivityFeed from "@/components/ActivityFeed";

export default function ActivityPage() {
  return (
    <main style={{ padding: "1.5rem", maxWidth: 900, margin: "0 auto" }}>
      <h1 style={{ marginBottom: "0.25rem" }}>Live Activity</h1>
      <p style={{ color: "#64748b", fontSize: "0.875rem", marginBottom: "1.5rem" }}>
        Real-time job events via WebSocket — falls back to polling if Redis is unavailable.
      </p>
      <ActivityFeed />
    </main>
  );
}
