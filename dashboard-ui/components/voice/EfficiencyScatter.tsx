"use client";

import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ZAxis, Legend,
} from "recharts";
import type { TooltipProps } from "recharts";
import type { VoiceCampaignBreakdown } from "@/types";

interface Props {
  data: VoiceCampaignBreakdown[];
  height?: number | string;
}

// The three voice agents — fixed colors, fixed order, no others rendered.
// label matches the value stored in call_events.voice_agent (exact case).
const CAMPAIGNS: { label: string; color: string }[] = [
  { label: "ColdLead", color: "#2563eb" },
  { label: "NewLead",  color: "#16a34a" },
  { label: "Inbound",  color: "#eab308" },
];

// Normalize incoming campaign/voice_agent field to canonical stored values.
// Accepts both old-style ("Cold Lead") and new-style ("ColdLead") spellings.
function normalizeCampaign(name: string): string | null {
  const n = name.trim().toLowerCase().replace(/\s+/g, "");
  if (n === "coldlead") return "ColdLead";
  if (n === "newlead")  return "NewLead";
  if (n === "inbound")  return "Inbound";
  return null;
}

function CustomTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload as VoiceCampaignBreakdown;
  if (!d) return null;
  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "0.6rem 0.875rem",
        boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
        fontSize: "0.95rem",
        minWidth: 200,
      }}
    >
      <div style={{ fontWeight: 700, color: "#1e293b", marginBottom: 6, fontSize: "1rem" }}>
        {normalizeCampaign(d.campaign) ?? d.campaign}
      </div>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <tbody>
          {[
            ["Pickup Rate",          `${d.pickup_rate.toFixed(1)}%`],
            ["Booked Appt %",        `${d.booking_rate.toFixed(1)}%`],
            ["Avg Calls per Day",    d.avg_calls_per_day.toFixed(1)],
            ["Total Calls (period)", String(d.total_calls)],
          ].map(([label, val]) => (
            <tr key={label}>
              <td style={{ color: "#64748b", paddingRight: "1rem", paddingBottom: 2 }}>{label}</td>
              <td style={{ textAlign: "right", fontWeight: 600, color: "#1e293b" }}>{val}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function EfficiencyScatter({ data, height = "100%" }: Props) {
  if (data.length === 0) {
    return (
      <div style={{ color: "#94a3b8", padding: "2rem", textAlign: "center", fontSize: "0.875rem" }}>
        No campaign data for this period
      </div>
    );
  }

  // Normalize incoming data to canonical campaign names and merge duplicates.
  // Only the three known campaigns appear; anything else is dropped.
  const merged: Record<string, VoiceCampaignBreakdown> = {};
  for (const d of data) {
    const label = normalizeCampaign(d.campaign);
    if (!label) continue;
    if (!merged[label]) {
      merged[label] = { ...d, campaign: label };
    } else {
      // Merge by summing calls, then recompute rates
      const prev = merged[label];
      const totalCalls = prev.total_calls + d.total_calls;
      merged[label] = {
        campaign: label,
        total_calls: totalCalls,
        pickup_rate:    totalCalls ? (prev.pickup_rate    * prev.total_calls + d.pickup_rate    * d.total_calls) / totalCalls : 0,
        booking_rate:   totalCalls ? (prev.booking_rate   * prev.total_calls + d.booking_rate   * d.total_calls) / totalCalls : 0,
        avg_calls_per_day: totalCalls ? (prev.avg_calls_per_day * prev.total_calls + d.avg_calls_per_day * d.total_calls) / totalCalls : 0,
      };
    }
  }

  // Render in fixed order: ColdLead, NewLead, Inbound — at most one bubble each
  const series = CAMPAIGNS
    .filter((c) => merged[c.label] !== undefined)
    .map((c) => ({ label: c.label, color: c.color, point: merged[c.label] }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 4, right: 24, left: 4, bottom: 28 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          type="number"
          dataKey="pickup_rate"
          name="Pickup Rate"
          stroke="#e2e8f0"
          tick={{ fill: "#64748b", fontSize: 13 }}
          tickFormatter={(v) => `${v}%`}
          domain={[0, "auto"]}
          label={{
            value: "Pickup Rate (%)",
            position: "insideBottom",
            offset: -20,
            style: { fill: "#94a3b8", fontSize: 13, fontWeight: 600 },
          }}
        />
        <YAxis
          type="number"
          dataKey="booking_rate"
          name="Booked Appt %"
          stroke="#e2e8f0"
          tick={{ fill: "#64748b", fontSize: 13 }}
          tickFormatter={(v) => `${v}%`}
          domain={[0, "auto"]}
          label={{
            value: "Booked Appt %",
            angle: -90,
            position: "insideLeft",
            offset: 12,
            style: { fill: "#94a3b8", fontSize: 13, fontWeight: 600 },
          }}
        />
        {/* ZAxis controls bubble size based on avg calls per day */}
        <ZAxis type="number" dataKey="avg_calls_per_day" range={[60, 800]} name="Avg Calls/Day" />
        <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: "3 3" }} />
        <Legend
          verticalAlign="top"
          height={26}
          wrapperStyle={{ fontSize: "0.85rem" }}
        />
        {series.map(({ label, color, point }) => (
          <Scatter
            key={label}
            name={label}
            data={[point]}
            fill={color}
            fillOpacity={0.8}
          />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  );
}
