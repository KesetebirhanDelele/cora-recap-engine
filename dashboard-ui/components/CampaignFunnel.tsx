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
  enrolled:               "#16a34a",
  re_engaged:             "#2563eb",
  callback_request:       "#7c3aed",
  interested_not_now:     "#0891b2",
  not_interested:         "#ea580c",
  do_not_call:            "#dc2626",
  partial_engagement:     "#d97706",
  human_transfer_request: "#9333ea",
  low_confidence_audio:   "#94a3b8",
};

const AXIS_STYLE = { fill: "#64748b", fontSize: 12 };
const LABEL_STYLE: React.CSSProperties = { fill: "#94a3b8", fontSize: 11 };
const TOOLTIP_STYLE = { background: "#ffffff", border: "1px solid #e2e8f0", color: "#1e293b", boxShadow: "0 4px 12px rgba(0,0,0,0.08)" };

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
      <h3 style={{ color: "#0f172a", marginBottom: "0.75rem", fontSize: "1rem", fontWeight: 700 }}>Intent Distribution</h3>
      {intentData.length === 0 ? (
        <p style={{ color: "#94a3b8" }}>No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={intentData} layout="vertical" margin={{ top: 4, right: 40, left: 8, bottom: 24 }}>
            <XAxis type="number" stroke="#e2e8f0" tick={AXIS_STYLE}>
              <Label value="Number of Calls" position="insideBottom" offset={-12} style={LABEL_STYLE} />
            </XAxis>
            <YAxis
              type="category"
              dataKey="name"
              width={160}
              stroke="#e2e8f0"
              tick={AXIS_STYLE}
            >
              <Label value="Intent" angle={-90} position="insideLeft" offset={10} style={LABEL_STYLE} />
            </YAxis>
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#0000000a" }} />
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

      <h3 style={{ color: "#0f172a", marginTop: "2rem", marginBottom: "0.75rem", fontSize: "1rem", fontWeight: 700 }}>Consent Distribution</h3>
      {consentData.length === 0 ? (
        <p style={{ color: "#94a3b8" }}>No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={consentData} margin={{ top: 4, right: 24, left: 8, bottom: 24 }}>
            <XAxis dataKey="name" stroke="#e2e8f0" tick={AXIS_STYLE}>
              <Label value="Consent Status" position="insideBottom" offset={-12} style={LABEL_STYLE} />
            </XAxis>
            <YAxis stroke="#e2e8f0" tick={AXIS_STYLE}>
              <Label value="Count" angle={-90} position="insideLeft" offset={10} style={LABEL_STYLE} />
            </YAxis>
            <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#0000000a" }} />
            <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
