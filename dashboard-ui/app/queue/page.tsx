import { fetchMetrics } from "@/lib/api";
import QueueTable from "@/components/QueueTable";

export const revalidate = 0;

export default async function QueuePage() {
  const metrics = await fetchMetrics();
  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Queue Health</h1>
      <QueueTable queue={metrics.queue} />
    </main>
  );
}
