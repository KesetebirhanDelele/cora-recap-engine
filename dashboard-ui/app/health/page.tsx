import { fetchHealth } from "@/lib/api";
import HealthTiles from "@/components/HealthTiles";

export const revalidate = 0;

export default async function HealthPage() {
  const health = await fetchHealth();
  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>System Health</h1>
      <HealthTiles health={health} />
    </main>
  );
}
