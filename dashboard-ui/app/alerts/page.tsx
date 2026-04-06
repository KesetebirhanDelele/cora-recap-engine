import PageShell from "@/components/PageShell";
import AlertsClient from "@/components/AlertsClient";

export const revalidate = 0;

export default function AlertsPage() {
  return (
    <PageShell
      title="Alerts"
      subtitle="Active threshold alerts — queue lag, error spikes, and worker status."
    >
      <AlertsClient />
    </PageShell>
  );
}
