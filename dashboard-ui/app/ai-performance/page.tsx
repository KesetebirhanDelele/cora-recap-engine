import { fetchMetrics } from "@/lib/api";
import CampaignFunnel from "@/components/CampaignFunnel";

export const revalidate = 0;

export default async function AiPerformancePage() {
  const metrics = await fetchMetrics();
  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>AI Performance</h1>
      <p style={{ color: "#94a3b8" }}>
        Blank Transcript Rate:{" "}
        <strong>
          {metrics.ai.blank_transcript_rate !== null
            ? `${(metrics.ai.blank_transcript_rate * 100).toFixed(1)}%`
            : "—"}
        </strong>
      </p>
      <CampaignFunnel ai={metrics.ai} />
    </main>
  );
}
