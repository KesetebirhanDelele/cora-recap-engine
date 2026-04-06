import { fetchMetrics } from "@/lib/api";
import KpiCards from "@/components/KpiCards";
import CampaignFunnel from "@/components/CampaignFunnel";

export const revalidate = 0;

export default async function KpisPage({
  searchParams,
}: {
  searchParams?: Promise<{ campaign?: string; from_date?: string; to_date?: string }>;
}) {
  const { campaign, from_date, to_date } = (await searchParams) ?? {};
  const metrics = await fetchMetrics({ campaign, from_date, to_date });

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>KPIs</h1>
      <KpiCards kpis={metrics.kpis} period={metrics.period} />
      <CampaignFunnel ai={metrics.ai} />
    </main>
  );
}
