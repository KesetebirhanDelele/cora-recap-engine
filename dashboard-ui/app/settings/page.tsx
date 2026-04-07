import PageShell from "@/components/PageShell";
import SettingsClient from "@/components/SettingsClient";

export const revalidate = 0;

export default function SettingsPage() {
  return (
    <PageShell
      title="Campaign Settings"
      subtitle="Calling windows, voicemail retry delays, messaging delays, and brand identity. Changes take effect immediately."
    >
      <SettingsClient />
    </PageShell>
  );
}
