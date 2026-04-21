import PageShell from "@/components/PageShell";
import QueueClient from "@/components/QueueClient";

export default function QueuePage() {
  return (
    <PageShell
      title="Queue Health"
      subtitle="Stuck jobs and expired worker leases requiring attention."
      fullWidth
    >
      <QueueClient />
    </PageShell>
  );
}
