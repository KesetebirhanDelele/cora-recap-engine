import PageShell from "@/components/PageShell";
import CampaignOverviewClient from "@/components/CampaignOverviewClient";

export const revalidate = 0;

export default function CampaignOverviewPage() {
  return (
    <PageShell
      title="Scheduled Actions"
      subtitle="Upcoming scheduled contact actions — calls, SMS, and email follow-ups within a date window."
      fullWidth
    >
      <CampaignOverviewClient />
    </PageShell>
  );
}
