import PageShell from "@/components/PageShell";
import CallbackFollowupsClient from "@/components/CallbackFollowupsClient";

export const revalidate = 0;

export default function CallbackFollowupsPage() {
  return (
    <PageShell
      title="Call-back Follow-ups"
      subtitle="Leads who asked for a person (failed transfer) or whose email went unanswered: what Cora planned, what was sent to staff, who was reached."
    >
      <CallbackFollowupsClient />
    </PageShell>
  );
}
