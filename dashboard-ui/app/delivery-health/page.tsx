import PageShell from "@/components/PageShell";
import DeliveryHealthClient from "@/components/DeliveryHealthClient";

export const revalidate = 0;

export default function DeliveryHealthPage() {
  return (
    <PageShell
      title="Delivery Health"
      subtitle="Per channel: what Cora handed over vs what was actually delivered. A channel with no delivery for 2.5 days raises an alert."
    >
      <DeliveryHealthClient />
    </PageShell>
  );
}
