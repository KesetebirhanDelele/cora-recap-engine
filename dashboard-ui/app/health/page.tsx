import { fetchHealth } from "@/lib/api";
import PageShell from "@/components/PageShell";
import HealthTiles from "@/components/HealthTiles";

export const revalidate = 0;

export default async function HealthPage() {
  const health = await fetchHealth();
  return (
    <PageShell title="System Health" subtitle="Live snapshot of queue, workers, and error rates.">
      <div
        style={{
          background: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          padding: "1rem 1.25rem",
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        }}
      >
        <HealthTiles health={health} />
      </div>
    </PageShell>
  );
}
