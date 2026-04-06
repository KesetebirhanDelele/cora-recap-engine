import PageShell from "@/components/PageShell";
import ActivityFeed from "@/components/ActivityFeed";

export default function ActivityPage() {
  return (
    <PageShell
      title="Live Activity"
      subtitle="Real-time job events via WebSocket — falls back to polling if Redis is unavailable."
      fullWidth
    >
      <ActivityFeed />
    </PageShell>
  );
}
