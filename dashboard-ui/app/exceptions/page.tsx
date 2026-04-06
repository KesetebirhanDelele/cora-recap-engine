import { fetchMetrics } from "@/lib/api";
import ExceptionQueue from "@/components/ExceptionQueue";

export const revalidate = 0;

export default async function ExceptionsPage() {
  const metrics = await fetchMetrics();
  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Exception Queue</h1>
      <ExceptionQueue queue={metrics.queue} />
    </main>
  );
}
