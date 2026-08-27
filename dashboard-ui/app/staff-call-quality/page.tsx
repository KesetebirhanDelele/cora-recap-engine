import PageShell from "@/components/PageShell";
import StaffCallQualityTable from "@/components/StaffCallQualityTable";
import { fetchStaffCallQuality } from "@/lib/api";
import type { StaffCallQualityResponse } from "@/types";

export const revalidate = 0;

const STAT_LABEL: React.CSSProperties = { fontSize: "0.75rem", fontWeight: 600, color: "#64748b" };
const STAT_VALUE: React.CSSProperties = { fontSize: "1.6rem", fontWeight: 800, color: "#0f172a", marginTop: 2 };

function StatTile({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderLeft: `3px solid ${accent ?? "#3b82f6"}`,
        borderRadius: 8,
        padding: "0.75rem 1rem",
        boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
      }}
    >
      <div style={STAT_LABEL}>{label}</div>
      <div style={STAT_VALUE}>{value}</div>
    </div>
  );
}

const INPUT_STYLE: React.CSSProperties = {
  border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.4rem 0.6rem",
  fontSize: "0.82rem", color: "#334155", background: "#fff",
};

function DateFilterForm({ from, to }: { from?: string; to?: string }) {
  const hasFilter = Boolean(from || to);
  return (
    <form
      method="GET"
      style={{
        display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "1rem",
        padding: "0.6rem 0.85rem", background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8,
      }}
    >
      <label style={{ fontSize: "0.78rem", fontWeight: 600, color: "#64748b" }}>From</label>
      <input type="date" name="from" defaultValue={from} style={INPUT_STYLE} />
      <label style={{ fontSize: "0.78rem", fontWeight: 600, color: "#64748b" }}>To</label>
      <input type="date" name="to" defaultValue={to} style={INPUT_STYLE} />
      <button
        type="submit"
        style={{
          border: "1px solid #3b82f6", background: "#3b82f6", color: "#fff",
          borderRadius: 6, padding: "0.4rem 0.9rem", fontSize: "0.8rem", fontWeight: 700, cursor: "pointer",
        }}
      >
        Filter
      </button>
      {hasFilter && (
        <a
          href="/staff-call-quality"
          style={{ fontSize: "0.78rem", color: "#64748b", textDecoration: "underline", marginLeft: "0.25rem" }}
        >
          Clear
        </a>
      )}
      <span style={{ marginLeft: "auto", fontSize: "0.72rem", color: "#94a3b8" }}>
        Always sorted newest first
      </span>
    </form>
  );
}

export default async function StaffCallQualityPage({
  searchParams,
}: {
  searchParams?: { from?: string; to?: string };
}) {
  const from = searchParams?.from || undefined;
  const to = searchParams?.to || undefined;

  let data: StaffCallQualityResponse | null = null;
  let error: string | null = null;
  try {
    data = await fetchStaffCallQuality({
      limit: 100,
      from_date: from ? `${from}T00:00:00Z` : undefined,
      to_date: to ? `${to}T23:59:59Z` : undefined,
    });
  } catch (e) {
    error = String(e);
  }

  return (
    <PageShell
      title="Staff Call Quality"
      subtitle="Sales-rep and support-staff calls pulled from GHL's native dialer, transcribed and scored (spec/23)."
    >
      <DateFilterForm from={from} to={to} />

      {error && (
        <div style={{ marginBottom: "1rem", padding: "0.75rem 1rem", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, color: "#dc2626", fontSize: "0.85rem" }}>
          Failed to load: {error}
        </div>
      )}

      {data && data.total_scanned === 0 && (
        <div style={{ marginBottom: "1.25rem", padding: "0.75rem 1rem", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, color: "#92400e", fontSize: "0.85rem" }}>
          {from || to
            ? "No calls in this date range."
            : <>No calls analyzed yet. This scan is off by default (<code>STAFF_CALL_QUALITY_SCAN_ENABLED=false</code>) —
              nothing will appear here until it's turned on and has had time to run.</>}
        </div>
      )}

      {data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "0.75rem", marginBottom: "1.25rem" }}>
            <StatTile label="Calls Scanned" value={data.total_scanned} accent="#3b82f6" />
            <StatTile label="Connected" value={data.total_connected} accent="#3b82f6" />
            <StatTile label="Analyzed" value={data.total_analyzed} accent="#3b82f6" />
            <StatTile label="Flagged" value={data.flagged_count} accent={data.flagged_count > 0 ? "#dc2626" : "#94a3b8"} />
            <StatTile
              label="Avg Score (Sales / Support)"
              value={`${data.avg_score_sales ?? "—"} / ${data.avg_score_support ?? "—"}`}
              accent="#8b5cf6"
            />
          </div>

          <StaffCallQualityTable rows={data.recent} />

          {data.recent.length >= 100 && (
            <div style={{ marginTop: "0.6rem", fontSize: "0.75rem", color: "#94a3b8" }}>
              Showing the 100 most recent{from || to ? " in this date range" : ""}. Narrow the date range above to see more of a specific period.
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}
