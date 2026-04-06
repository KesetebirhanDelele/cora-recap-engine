import { fetchMetrics } from "@/lib/api";

export const revalidate = 0;

export default async function CrmHealthPage() {
  const metrics = await fetchMetrics();
  const { crm } = metrics;

  const fmt = (v: number | null) =>
    v === null ? "—" : `${(v * 100).toFixed(1)}%`;

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>CRM Health</h1>
      <table style={{ borderCollapse: "collapse", width: "100%", maxWidth: 500 }}>
        <tbody>
          <tr>
            <td style={{ padding: "0.5rem 1rem 0.5rem 0", color: "#94a3b8" }}>GHL Task Success Rate</td>
            <td style={{ fontWeight: "bold" }}>{fmt(crm.ghl_task_success_rate)}</td>
          </tr>
          <tr>
            <td style={{ padding: "0.5rem 1rem 0.5rem 0", color: "#94a3b8" }}>GHL VM Update Success Rate</td>
            <td style={{ fontWeight: "bold" }}>{fmt(crm.ghl_vm_update_success_rate)}</td>
          </tr>
          <tr>
            <td style={{ padding: "0.5rem 1rem 0.5rem 0", color: "#94a3b8" }}>Shadow Write Count (period)</td>
            <td style={{ fontWeight: "bold" }}>{crm.ghl_shadow_write_count}</td>
          </tr>
        </tbody>
      </table>
    </main>
  );
}
