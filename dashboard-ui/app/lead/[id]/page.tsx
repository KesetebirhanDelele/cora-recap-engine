import { fetchLeadTrace } from "@/lib/api";
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

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Lead Pipeline Trace</h1>
      <p style={{ color: "#94a3b8" }}>
        {id.startsWith("+") || /^\d+$/.test(id) ? "Phone" : "Contact ID"}: {id}
      </p>
      {error ? (
        <p style={{ color: "#f87171" }}>{error}</p>
      ) : (
        <PipelineTrace trace={trace!} />
      )}
    </main>
  );
}
