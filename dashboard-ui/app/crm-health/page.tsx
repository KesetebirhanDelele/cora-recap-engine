import { fetchMetrics } from "@/lib/api";
import PageShell from "@/components/PageShell";

export const revalidate = 0;

export default async function CrmHealthPage() {
  const metrics = await fetchMetrics();
  const { crm } = metrics;
  const fmt = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);

  const rows: { label: string; value: string; warn?: boolean }[] = [
    {
      label: "GHL Task Success Rate",
      value: fmt(crm.ghl_task_success_rate),
      warn: crm.ghl_task_success_rate !== null && crm.ghl_task_success_rate < 0.9,
    },
    {
      label: "GHL VM Update Success Rate",
      value: fmt(crm.ghl_vm_update_success_rate),
      warn: crm.ghl_vm_update_success_rate !== null && crm.ghl_vm_update_success_rate < 0.9,
    },
    {
      label: "Shadow Write Count (period)",
      value: crm.ghl_shadow_write_count.toLocaleString(),
    },
  ];

  return (
    <PageShell
      title="CRM Health"
      subtitle="GHL integration success rates and shadow write activity."
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", maxWidth: 520 }}>
        {rows.map(({ label, value, warn }) => (
          <div
            key={label}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              background: "#ffffff",
              border: `1px solid ${warn ? "#fde68a" : "#e2e8f0"}`,
              borderLeft: `3px solid ${warn ? "#f59e0b" : "#e2e8f0"}`,
              boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
              borderRadius: 8,
              padding: "0.75rem 1rem",
            }}
          >
            <span style={{ fontSize: "0.82rem", color: "#64748b" }}>{label}</span>
            <span style={{ fontSize: "1.15rem", fontWeight: 700, color: warn ? "#d97706" : "#0f172a" }}>
              {value}
            </span>
          </div>
        ))}
      </div>
    </PageShell>
  );
}
