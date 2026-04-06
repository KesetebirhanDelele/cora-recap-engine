import { fetchLeadTrace } from "@/lib/api";
import PageShell from "@/components/PageShell";
import PipelineTrace from "@/components/PipelineTrace";

export const revalidate = 0;

export default async function LeadTracePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ phone?: string }>;
}) {
  const { id: rawId } = await params;
  const id = decodeURIComponent(rawId);
  const { phone } = (await searchParams) ?? {};

  let trace = null;
  let error: string | null = null;

  try {
    trace = await fetchLeadTrace(id, phone);
  } catch (e: unknown) {
    const err = e as { status?: number; message?: string };
    error = err.status === 404 ? "Lead not found" : String(e);
  }

  const labelType = id.startsWith("+") || /^\d+$/.test(id) ? "Phone" : "Contact ID";

  return (
    <PageShell
      title="Lead Pipeline Trace"
      subtitle={`${labelType}: ${id}`}
    >
      {error ? (
        <div
          style={{
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderLeft: "3px solid #ef4444",
            borderRadius: 8,
            padding: "1rem 1.25rem",
            color: "#dc2626",
            fontSize: "0.875rem",
          }}
        >
          {error}
        </div>
      ) : (
        <PipelineTrace trace={trace!} />
      )}
    </PageShell>
  );
}
