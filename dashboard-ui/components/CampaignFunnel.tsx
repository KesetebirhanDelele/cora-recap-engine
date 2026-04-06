"use client";

import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, Label,
} from "recharts";
import type { MetricsResponse } from "@/types";

interface Props {
  ai: MetricsResponse["ai"];
}

const INTENT_COLORS: Record<string, string> = {
  enrolled:                "#22c55e",
  re_engaged:              "#3b82f6",
  callback_request:        "#8b5cf6",
  interested_not_now:      "#06b6d4",
  not_interested:          "#f97316",
  do_not_call:             "#ef4444",
  partial_engagement:      "#eab308",
  human_transfer_request:  "#a855f7",
  low_confidence_audio:    "#64748b",
};

const AXIS_STYLE = { fill: "#cbd5e1", fontSize: 12 };
const LABEL_STYLE = { fill: "#94a3b8", fontSize: 11 };
const TOOLTIP_STYLE = { background: "#1e293b", border: "1px solid #334155", color: "#f1f5f9" };

export default function CampaignFunnel({ ai }: Props) {
  const intentData = Object.entries(ai.intent_distribution)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({ name: name.replace(/_/g, " "), count }));

  const consentData = Object.entries(ai.consent_distribution).map(([name, count]) => ({
    name,
    count,
  }));

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <h3 style={{ color: "#f1f5f9", marginBottom: "0.75rem" }}>Intent Distribution</h3>
      {intentData.length === 0 ? (
        <p style={{ color: "#64748b" }}>No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={intentData} layout="vertical" margin={{ top: 4, right: 40, left: 8, bottom: 24 }}>
            <XAxis type="number" stroke="#475569" tick={AXIS_STYLE}>
              <Label value="Number of Calls" position="insideBottom" offset={-12} style={LABEL_STYLE} />
            </XAxis>
            <YAxis
              type="category"
              dataKey="name"
              width={160}
              stroke="#475569"
              tick={AXIS_STYLE}
            >
              <Label value="Intent" angle={-90} position="insideLeft" offset={10} style={LABEL_STYLE} />
            </YAxis>
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#ffffff10" }} />
            <Bar dataKey="count" radius={[0, 4, 4, 0]}>
              {intentData.map((entry) => (
                <Cell
                  key={entry.name}
                  fill={INTENT_COLORS[entry.name.replace(/ /g, "_")] ?? "#3b82f6"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}

      <h3 style={{ color: "#f1f5f9", marginTop: "2rem", marginBottom: "0.75rem" }}>Consent Distribution</h3>
      {consentData.length === 0 ? (
        <p style={{ color: "#64748b" }}>No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={consentData} margin={{ top: 4, right: 24, left: 8, bottom: 24 }}>
            <XAxis dataKey="name" stroke="#475569" tick={AXIS_STYLE}>
              <Label value="Consent Status" position="insideBottom" offset={-12} style={LABEL_STYLE} />
            </XAxis>
            <YAxis stroke="#475569" tick={AXIS_STYLE}>
              <Label value="Count" angle={-90} position="insideLeft" offset={10} style={LABEL_STYLE} />
            </YAxis>
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#ffffff10" }} />
            <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
