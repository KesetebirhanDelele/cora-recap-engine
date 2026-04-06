import PageShell from "@/components/PageShell";
import SystemAnomalies from "@/components/SystemAnomalies";

export const revalidate = 0;

export default function SystemAnomaliesPage() {
  return (
    <PageShell
      title="System Anomalies"
      subtitle="Detected spikes, recurring failure patterns, and cluster analysis — aggregated intelligence, not individual records."
      fullWidth
    >
      <SystemAnomalies />
    </PageShell>
  );
}
