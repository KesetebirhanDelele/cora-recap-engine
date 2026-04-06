import { fetchMetrics } from "@/lib/api";
import PageShell from "@/components/PageShell";
import QueueTable from "@/components/QueueTable";

export const revalidate = 0;

export default async function QueuePage() {
  const metrics = await fetchMetrics();
  return (
    <PageShell
      title="Queue Health"
      subtitle="Stuck jobs and expired worker leases requiring attention."
      fullWidth
    >
      <QueueTable queue={metrics.queue} />
    </PageShell>
  );
}
