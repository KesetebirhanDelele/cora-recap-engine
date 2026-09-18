import PageShell from "@/components/PageShell";
import TagAiColdLeadsClient from "@/components/TagAiColdLeadsClient";

export const revalidate = 0;

export default function AiColdLeadTaggingPage() {
  return (
    <PageShell
      title="AI Cold Lead Tagging"
      subtitle="Daily batch tagging of stale, contactable leads — activity and backlog."
    >
      <TagAiColdLeadsClient />
    </PageShell>
  );
}
