import { fetchAlerts } from "@/lib/api";
import AlertBanner from "@/components/AlertBanner";
import type { AlertStatus } from "@/types";

export const revalidate = 0;

export default async function AlertsPage({
  searchParams,
}: {
  searchParams?: Promise<{ status?: string }>;
}) {
  const { status } = (await searchParams) ?? {};
  const statusParam = (status ?? "active") as AlertStatus;
  const { alerts } = await fetchAlerts(statusParam);

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Alerts</h1>
      <AlertBanner alerts={alerts} />
    </main>
  );
}
