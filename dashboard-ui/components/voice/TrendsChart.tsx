"use client";

import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import type { TooltipProps } from "recharts";
import TooltipKpiTable from "./TooltipKpiTable";
import type { VoiceTimeSeriesPoint } from "@/types";

interface Props {
  data: VoiceTimeSeriesPoint[];
  /** Height passed to ResponsiveContainer. Use "100%" when parent has flex:1+minHeight:0. */
  height?: number | string;
}

const AXIS_STYLE = { fill: "#64748b", fontSize: 11 };
const LABEL_STYLE: React.CSSProperties = {
  fill: "#94a3b8",
  fontSize: 11,
  fontWeight: 600,
};

function CustomTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload as VoiceTimeSeriesPoint;
  if (!point) return null;
  return <TooltipKpiTable point={point} label={label as string} />;
}

export default function TrendsChart({ data, height = "100%" }: Props) {
  if (data.length === 0) {
    return (
      <div style={{ color: "#94a3b8", padding: "2rem", textAlign: "center", fontSize: "0.875rem" }}>
        No data for this period
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 4, right: 20, left: 4, bottom: 20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />

        {/* Left Y axis: call counts (bars) */}
        <YAxis
          yAxisId="calls"
          orientation="left"
          stroke="#e2e8f0"
          tick={AXIS_STYLE}
          label={{
            value: "Total Calls",
            angle: -90,
            position: "insideLeft",
            offset: 12,
            style: LABEL_STYLE,
          }}
        />

        {/* Right Y axis: percentage (lines) */}
        <YAxis
          yAxisId="pct"
          orientation="right"
          stroke="#e2e8f0"
          tick={AXIS_STYLE}
          tickFormatter={(v) => `${v}%`}
          domain={[0, 100]}
          label={{
            value: "Rate (%)",
            angle: 90,
            position: "insideRight",
            offset: 12,
            style: LABEL_STYLE,
          }}
        />

        <XAxis
          dataKey="date"
          stroke="#e2e8f0"
          tick={AXIS_STYLE}
          label={{
            value: "Week",
            position: "insideBottom",
            offset: -16,
            style: LABEL_STYLE,
          }}
        />

        <Tooltip content={<CustomTooltip />} />

        <Legend
          verticalAlign="top"
          height={24}
          wrapperStyle={{ fontSize: "0.68rem" }}
        />

        {/* Stacked bars: call volumes by campaign */}
        <Bar yAxisId="calls" dataKey="cold"     stackId="calls" name="Cold"    fill="#2563eb" radius={[0,0,0,0]} />
        <Bar yAxisId="calls" dataKey="inbound"  stackId="calls" name="Inbound" fill="#f59e0b" radius={[0,0,0,0]} />
        <Bar yAxisId="calls" dataKey="new_lead" stackId="calls" name="New"     fill="#16a34a" radius={[2,2,0,0]} />

        {/* Lines: rate metrics */}
        <Line yAxisId="pct" type="monotone" dataKey="completion_rate" name="Completion %"   stroke="#0ea5e9" strokeWidth={2} dot={false} />
        <Line yAxisId="pct" type="monotone" dataKey="pickup_rate"     name="Pickup %"       stroke="#10b981" strokeWidth={2} dot={false} />
        <Line yAxisId="pct" type="monotone" dataKey="voicemail_rate"  name="Voicemail %"    stroke="#8b5cf6" strokeWidth={2} dot={false} />
        <Line yAxisId="pct" type="monotone" dataKey="failed_rate"     name="Failed %"       stroke="#ef4444" strokeWidth={2} dot={false} strokeDasharray="4 2" />
        <Line yAxisId="pct" type="monotone" dataKey="booking_rate"    name="Booking Rate %" stroke="#7c3aed" strokeWidth={2} dot={false} strokeDasharray="6 2" />

        <ReferenceLine yAxisId="pct" y={0} stroke="#e2e8f0" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
