import PageShell from "@/components/PageShell";
import SmsMonitorClient from "@/components/SmsMonitorClient";

export const revalidate = 0;

export default function SmsMonitorPage() {
  return (
    <PageShell
      title="SMS Monitor"
      subtitle="Every text passes a pre-send gate: content rules, a hard daily budget of 999 segments (US Pacific day) and provider pacing."
    >
      <SmsMonitorClient />
    </PageShell>
  );
}
