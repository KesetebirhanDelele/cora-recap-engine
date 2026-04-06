import PageShell from "@/components/PageShell";
import ExceptionQueue from "@/components/ExceptionQueue";

export const revalidate = 0;

export default function ExceptionsPage() {
  return (
    <PageShell
      title="Exception Queue"
      subtitle="Open exception records — click any row to expand context and details."
      fullWidth
    >
      <ExceptionQueue />
    </PageShell>
  );
}
