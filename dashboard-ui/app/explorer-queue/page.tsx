import PageShell from "@/components/PageShell";
import ExplorerQueueClient from "@/components/ExplorerQueueClient";

export const revalidate = 0;

export default function ExplorerQueuePage() {
  return (
    <PageShell
      title="Explorer Call Queue"
      subtitle="Ranked Explorer leads tagged in GHL for Rose — queue size, who has been called, nightly runs."
    >
      <ExplorerQueueClient />
    </PageShell>
  );
}
