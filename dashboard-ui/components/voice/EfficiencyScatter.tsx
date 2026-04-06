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

const CAMPAIGN_COLORS: Record<string, string> = {
  "Cold Lead": "#2563eb",
  "Inbound":   "#f59e0b",
  "New Lead":  "#16a34a",
};

function campaignColor(name: string): string {
  for (const [key, color] of Object.entries(CAMPAIGN_COLORS)) {
    if (name.toLowerCase().includes(key.toLowerCase().split(" ")[0])) return color;
  }
  return "#64748b";
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
        fontSize: "0.8rem",
        minWidth: 190,
      }}
    >
      <div style={{ fontWeight: 700, color: "#1e293b", marginBottom: 6, fontSize: "0.85rem" }}>
        {d.campaign}
      </div>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <tbody>
          {[
            ["Pickup Rate",          `${d.pickup_rate.toFixed(1)}%`],
            ["Booked Appt %",        `${d.booking_rate.toFixed(1)}%`],
            ["Avg Calls per Day",    d.avg_calls_per_day.toFixed(1)],
            ["Total Calls",          String(d.total_calls)],
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

  // Group data by campaign type for separate Scatter series (needed for legend)
  const grouped: Record<string, VoiceCampaignBreakdown[]> = {};
  for (const d of data) {
    grouped[d.campaign] = grouped[d.campaign] ?? [];
    grouped[d.campaign].push(d);
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 4, right: 24, left: 4, bottom: 28 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          type="number"
          dataKey="pickup_rate"
          name="Pickup Rate"
          stroke="#e2e8f0"
          tick={{ fill: "#64748b", fontSize: 11 }}
          tickFormatter={(v) => `${v}%`}
          domain={[0, "auto"]}
          label={{
            value: "Pickup Rate (%)",
            position: "insideBottom",
            offset: -20,
            style: { fill: "#94a3b8", fontSize: 11, fontWeight: 600 },
          }}
        />
        <YAxis
          type="number"
          dataKey="booking_rate"
          name="Booked Appt %"
          stroke="#e2e8f0"
          tick={{ fill: "#64748b", fontSize: 11 }}
          tickFormatter={(v) => `${v}%`}
          domain={[0, "auto"]}
          label={{
            value: "Booked Appt %",
            angle: -90,
            position: "insideLeft",
            offset: 12,
            style: { fill: "#94a3b8", fontSize: 11, fontWeight: 600 },
          }}
        />
        {/* ZAxis controls bubble size based on total_calls */}
        <ZAxis type="number" dataKey="total_calls" range={[60, 800]} name="Total Calls" />
        <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: "3 3" }} />
        <Legend
          verticalAlign="top"
          height={22}
          wrapperStyle={{ fontSize: "0.68rem" }}
        />
        {Object.entries(grouped).map(([campaign, points]) => (
          <Scatter
            key={campaign}
            name={campaign}
            data={points}
            fill={campaignColor(campaign)}
            fillOpacity={0.75}
          />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  );
}
