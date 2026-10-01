import PageShell from "@/components/PageShell";
import OptoutsClient from "@/components/OptoutsClient";

export const revalidate = 0;

export default function OptoutsPage() {
  return (
    <PageShell
      title="Opt-outs & DND"
      subtitle="Leads who asked us to stop — on a call, by text or by email. Clear requests are applied to GHL's real DND automatically; unclear ones wait here for you."
    >
      <OptoutsClient />
    </PageShell>
  );
}
