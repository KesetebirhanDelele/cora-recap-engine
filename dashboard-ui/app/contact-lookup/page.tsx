import PageShell from "@/components/PageShell";
import ContactLookupClient from "@/components/ContactLookupClient";

export const revalidate = 0;

export default function ContactLookupPage({
  searchParams,
}: {
  searchParams?: { phone?: string; id?: string };
}) {
  const seed = searchParams?.phone ?? searchParams?.id ?? undefined;
  return (
    <PageShell
      title="Contact Drill-Down"
      subtitle="Inspect all data for a single contact — lead state, calls, shadow actions, jobs, messages, and exceptions."
      fullWidth
    >
      <ContactLookupClient contactId={seed} />
    </PageShell>
  );
}
