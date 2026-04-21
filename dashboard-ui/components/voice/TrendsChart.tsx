"use client";

import { useState, useMemo } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import type { TooltipProps } from "recharts";
import TooltipKpiTable from "./TooltipKpiTable";
import type { VoiceTimeSeriesPoint } from "@/types";

interface Props {
  data: VoiceTimeSeriesPoint[];
  height?: number | string;
}

// Augmented point with per-campaign calls-per-day fields for the Y-axis
type ChartPoint = VoiceTimeSeriesPoint & {
  cold_cpd: number;
  inbound_cpd: number;
  new_lead_cpd: number;
};

const AXIS_STYLE = { fill: "#64748b", fontSize: 13 };
const LABEL_STYLE: React.CSSProperties = { fill: "#94a3b8", fontSize: 13, fontWeight: 600 };

/** Formats an ISO week-start date as "MM/DD–MM/DD" (start to +6 days). */
function fmtWeek(dateStr: string): string {
  const start = new Date(dateStr + "T00:00:00Z");
  const end   = new Date(start.getTime() + 6 * 24 * 60 * 60 * 1000);
  const f = (d: Date) =>
    `${String(d.getUTCMonth() + 1).padStart(2, "0")}/${String(d.getUTCDate()).padStart(2, "0")}`;
  return `${f(start)}-${f(end)}`;
}

interface CustomTooltipProps extends TooltipProps<number, string> {
  activeSeries?: string | null;
}

function CustomTooltip({ active, payload, label, activeSeries }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload as ChartPoint;
  if (!point) return null;
  return <TooltipKpiTable point={point} label={label as string} focusSeries={activeSeries ?? null} />;
}

export default function TrendsChart({ data, height = "100%" }: Props) {
  const [activeSeries, setActiveSeries] = useState<string | null>(null);

  // Derive avg-calls-per-day bars per campaign from weekly totals ÷ 7
  const chartData: ChartPoint[] = useMemo(
    () =>
      data.map((p) => ({
        ...p,
        cold_cpd:     +(p.cold     / 7).toFixed(1),
        inbound_cpd:  +(p.inbound  / 7).toFixed(1),
        new_lead_cpd: +(p.new_lead / 7).toFixed(1),
      })),
    [data]
  );

  if (data.length === 0) {
    return (
      <div style={{ color: "#94a3b8", padding: "2rem", textAlign: "center", fontSize: "0.875rem" }}>
        No data for this period
      </div>
    );
  }

  const clearSeries = () => setActiveSeries(null);

  function activeDot(seriesKey: string, stroke: string) {
    return {
      r: 5,
      strokeWidth: 0,
      fill: stroke,
      onMouseEnter: () => setActiveSeries(seriesKey),
      onMouseLeave: clearSeries,
    };
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={chartData} margin={{ top: 4, right: 20, left: 4, bottom: 20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />

        <YAxis
          yAxisId="calls"
          orientation="left"
          stroke="#e2e8f0"
          tick={AXIS_STYLE}
          label={{ value: "Calls / Day", angle: -90, position: "insideLeft", offset: 12, style: LABEL_STYLE }}
        />
        <YAxis
          yAxisId="pct"
          orientation="right"
          stroke="#e2e8f0"
          tick={AXIS_STYLE}
          tickFormatter={(v) => `${v}%`}
          domain={[0, 100]}
          label={{ value: "Rate (%)", angle: 90, position: "insideRight", offset: 12, style: LABEL_STYLE }}
        />
        <XAxis
          dataKey="date"
          stroke="#e2e8f0"
          tick={AXIS_STYLE}
          tickFormatter={fmtWeek}
          label={{ value: "Week", position: "insideBottom", offset: -16, style: LABEL_STYLE }}
        />

        <Tooltip content={<CustomTooltip activeSeries={activeSeries} />} />
        <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: "0.85rem" }} />

        {/* Stacked bars — avg calls per day per campaign */}
        <Bar yAxisId="calls" dataKey="cold_cpd"     stackId="calls" name="Cold Lead" fill="#2563eb" radius={[0,0,0,0]}
          onMouseEnter={() => setActiveSeries("cold")}     onMouseLeave={clearSeries} />
        <Bar yAxisId="calls" dataKey="inbound_cpd"  stackId="calls" name="Inbound"   fill="#eab308" radius={[0,0,0,0]}
          onMouseEnter={() => setActiveSeries("inbound")}  onMouseLeave={clearSeries} />
        <Bar yAxisId="calls" dataKey="new_lead_cpd" stackId="calls" name="New Lead"  fill="#16a34a" radius={[2,2,0,0]}
          onMouseEnter={() => setActiveSeries("new_lead")} onMouseLeave={clearSeries} />

        {/* Rate lines */}
        <Line yAxisId="pct" type="monotone" dataKey="completion_rate" name="Completion %"   stroke="#0ea5e9" strokeWidth={2} dot={false} activeDot={activeDot("completion_rate", "#0ea5e9")} />
        <Line yAxisId="pct" type="monotone" dataKey="pickup_rate"     name="Pickup %"       stroke="#10b981" strokeWidth={2} dot={false} activeDot={activeDot("pickup_rate",     "#10b981")} />
        <Line yAxisId="pct" type="monotone" dataKey="voicemail_rate"  name="Voicemail %"    stroke="#8b5cf6" strokeWidth={2} dot={false} activeDot={activeDot("voicemail_rate",  "#8b5cf6")} />
        <Line yAxisId="pct" type="monotone" dataKey="failed_rate"     name="Failed %"       stroke="#ef4444" strokeWidth={2} dot={false} strokeDasharray="4 2" activeDot={activeDot("failed_rate", "#ef4444")} />
        <Line yAxisId="pct" type="monotone" dataKey="booking_rate"    name="Booking Rate %" stroke="#7c3aed" strokeWidth={2} dot={false} strokeDasharray="6 2" activeDot={activeDot("booking_rate", "#7c3aed")} />

        <ReferenceLine yAxisId="pct" y={0} stroke="#e2e8f0" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
