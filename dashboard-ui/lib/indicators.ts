/**
 * Utility functions for navigation card indicators.
 *
 * Each card shows a compact indicator:  [value label] ↑/↓/→
 *
 * Color semantics:
 *   green   — good state
 *   yellow  — caution
 *   red     — action needed
 *   default — neutral (no severity judgment)
 */

import type { CardMetricsResponse } from "@/types";

export type TrendArrow = "↑" | "↓" | "→";
export type IndicatorColor = "green" | "yellow" | "red" | "default";

export interface CardIndicator {
  text: string;        // formatted value + label, e.g. "17 events/min"
  trend: TrendArrow;
  color: IndicatorColor;
}

// ── Trend ─────────────────────────────────────────────────────────────────────

/** Direction of change: ↑ if current > previous, ↓ if less, → if equal/unknown. */
export function getTrend(
  current: number | null | undefined,
  previous: number | null | undefined,
): TrendArrow {
  if (current == null || previous == null || previous === 0) return "→";
  if (current > previous) return "↑";
  if (current < previous) return "↓";
  return "→";
}

// ── Color coding ──────────────────────────────────────────────────────────────

/** Positive metrics — higher is better (rates, percentages). */
export function getPositiveColor(value: number | null | undefined): IndicatorColor {
  if (value == null) return "default";
  if (value >= 0.7) return "green";
  if (value >= 0.4) return "yellow";
  return "red";
}

/** Negative metrics — lower is better (counts, backlogs). */
export function getNegativeColor(value: number | null | undefined): IndicatorColor {
  if (value == null) return "default";
  if (value <= 10) return "green";
  if (value <= 30) return "yellow";
  return "red";
}

/** Config health — string enum. */
export function getConfigColor(value: string | null | undefined): IndicatorColor {
  if (value === "healthy") return "green";
  if (value === "warning") return "yellow";
  return "red";
}

// ── Formatters ────────────────────────────────────────────────────────────────

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Math.round(v * 100)}%`;
}

function fmtK(v: number | null | undefined): string {
  if (v == null) return "—";
  const n = Number(v);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

// ── Card → metric mapping ─────────────────────────────────────────────────────

type ColorType = "positive" | "negative" | "neutral" | "config";

interface CardMetricConfig {
  key: keyof Omit<CardMetricsResponse, "computed_at">;
  format: (v: number | string | null | undefined) => string;
  colorType: ColorType;
}

const CARD_METRIC_MAP: Record<string, CardMetricConfig> = {
  "/activity":           { key: "events_per_min",             format: (v) => `${v ?? "—"} events/min`,    colorType: "neutral"  },
  "/exceptions":         { key: "open_exceptions",            format: (v) => `${v ?? "—"} open issues`,   colorType: "negative" },
  "/queue":              { key: "backlog_size",               format: (v) => `${v ?? "—"} backlog`,        colorType: "negative" },
  "/alerts":             { key: "active_alerts",              format: (v) => `${v ?? "—"} active alerts`, colorType: "negative" },
  "/contact-lookup":     { key: "lookup_rate",                format: (v) => `${v ?? "—"} calls/hr`,      colorType: "neutral"  },
  "/settings":           { key: "config_health",              format: (v) => `${v ?? "—"} config`,         colorType: "config"   },
  "/voice-performance":  { key: "pickup_rate",                format: (v) => `${pct(v as number | null)} pickup`,      colorType: "positive" },
  "/engagement-analysis": { key: "meaningful_engagement_rate", format: (v) => `${pct(v as number | null)} engagement`,  colorType: "positive" },
  "/conversion-funnel":  { key: "urgent_leads_count",         format: (v) => `${v ?? "—"} urgent leads`,              colorType: "negative" },
  "/lead-lifecycle":     { key: "active_leads",               format: (v) => `${fmtK(v as number | null)} active`,     colorType: "neutral"  },
  "/campaign-overview":  { key: "active_leads",               format: (v) => `${fmtK(v as number | null)} leads`,      colorType: "neutral"  },
  "/crm-health":         { key: "sync_success_rate",          format: (v) => `${pct(v as number | null)} sync`,        colorType: "positive" },
  "/system-anomalies":   { key: "anomaly_count",              format: (v) => `${v ?? "—"} anomalies`,     colorType: "negative" },
};

/** Compute the full indicator (text + trend + color) for a nav card href. */
export function computeIndicator(
  href: string,
  cardMetrics: CardMetricsResponse | null,
): CardIndicator | undefined {
  if (!cardMetrics) return undefined;

  const config = CARD_METRIC_MAP[href];
  if (!config) return undefined;

  const metric = cardMetrics[config.key];
  if (!metric) return undefined;

  const { value, previous_value } = metric;
  const text = config.format(value);

  let trend: TrendArrow = "→";
  let color: IndicatorColor = "default";

  if (config.colorType === "config") {
    color = getConfigColor(value as string | null);
    // No trend for config health (string enum)
  } else {
    const numVal  = typeof value          === "number" ? value          : null;
    const numPrev = typeof previous_value === "number" ? previous_value : null;

    trend = getTrend(numVal, numPrev);

    if (config.colorType === "positive") {
      color = getPositiveColor(numVal);
    } else if (config.colorType === "negative") {
      color = getNegativeColor(numVal);
    }
    // neutral: color stays "default"
  }

  return { text, trend, color };
}

// ── CSS color value ───────────────────────────────────────────────────────────

export const INDICATOR_COLORS: Record<IndicatorColor, string> = {
  green:   "#16a34a",
  yellow:  "#d97706",
  red:     "#dc2626",
  default: "#64748b",
};
