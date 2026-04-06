import PageShell from "@/components/PageShell";
import ExceptionsMonitor from "@/components/ExceptionQueue";

export const revalidate = 0;

export default function ExceptionsPage() {
  return (
    <PageShell
      title="Exceptions Monitor"
      subtitle="Real-time operational issues requiring attention — resolve, ignore, or investigate each exception."
      fullWidth
    >
      <ExceptionsMonitor />
    </PageShell>
  );
}
