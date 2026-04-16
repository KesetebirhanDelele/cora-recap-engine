import PageShell from "@/components/PageShell";
import SystemControlsClient from "@/components/SystemControlsClient";

export const revalidate = 0;

export default function SystemControlsPage() {
  return (
    <PageShell
      title="System Controls"
      subtitle="Toggle shadow / live mode, enable GHL writes, and pause the system. All changes are audited and take effect immediately without a restart."
    >
      <SystemControlsClient />
    </PageShell>
  );
}
