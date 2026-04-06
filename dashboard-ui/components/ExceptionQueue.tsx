"use client";

/**
 * ExceptionsMonitor — Exceptions Monitor page component.
 *
 * Real-time operational issue queue — raw records requiring operator action.
 * NOT for pattern analysis or aggregated insights (see SystemAnomalies for that).
 *
 * Sections:
 *   1. Filters: date range, exception type, status
 *   2. Metrics summary: Today / Open / Resolved (24h)
 *   3. Trend chart: daily counts grouped by category
 *   4. Grouped table: expandable rows sorted by newest
 */

import { useEffect, useState, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid,
} from "recharts";
import {
  fetchExceptions,
  fetchExceptionTrend,
  resolveException,
  ignoreException,
  bulkIgnoreExceptions,
} from "@/lib/api";
import DateRangePicker from "@/components/voice/DateRangePicker";
import type { ExceptionRecord, ExceptionGroup, ExceptionTrendPoint } from "@/types";

// ── Type metadata ─────────────────────────────────────────────────────────────

const CATEGORY: Record<string, string> = {
  unknown_call_status:       "Call",
  call_pending:              "Call",
  call_processing_failed:    "Call",
  identity_resolution:       "Call",
  tier_invalid:              "Call",
  openai_error:              "AI",
  openai_failed:             "AI",
  ghl_update_failed:         "CRM",
  ghl_auth_failed:           "CRM",
  ghl_write_failed:          "CRM",
  webhook_failure:           "System",
  job_timeout:               "System",
  data_validation_failure:   "System",
  retry_budget_exhausted:    "System",
  postgres_write_failed:     "System",
};

const CATEGORY_COLOR: Record<string, string> = {
  Call:   "#2563eb",
  AI:     "#7c3aed",
  CRM:    "#0891b2",
  System: "#64748b",
};

const ALL_TYPES = Object.keys(CATEGORY);

function getCategory(type: string): string {
  return CATEGORY[type] ?? "System";
}

// ── Card style per severity ───────────────────────────────────────────────────

function severityStyle(severity: string) {
  if (severity === "critical") {
    return { bg: "#fef2f2", border: "#fecaca", accent: "#ef4444", text: "#dc2626", badgeBg: "#fee2e2" };
  }
  return { bg: "#eff6ff", border: "#bfdbfe", accent: "#3b82f6", text: "#1d4ed8", badgeBg: "#dbeafe" };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function Chip({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 9999,
        fontSize: "0.68rem",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color,
        background: bg,
        border: `1px solid ${color}30`,
      }}
    >
      {label}
    </span>
  );
}

function ActionBtn({ label, onClick, color, disabled }: { label: string; onClick: () => void; color: string; disabled?: boolean }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      disabled={disabled}
      style={{
        padding: "3px 10px",
        borderRadius: 5,
        border: `1px solid ${color}40`,
        background: `${color}0e`,
        color,
        fontSize: "0.7rem",
        fontWeight: 600,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.4 : 1,
        transition: "background 0.12s",
      }}
    >
      {label}
    </button>
  );
}

// ── Metrics summary bar ───────────────────────────────────────────────────────

function MetricsSummary({ today, open, resolved24h }: { today: number; open: number; resolved24h: number }) {
  const items = [
    { label: "Today", value: today, color: "#2563eb", bg: "#eff6ff" },
    { label: "Open", value: open, color: open > 0 ? "#dc2626" : "#16a34a", bg: open > 0 ? "#fef2f2" : "#f0fdf4" },
    { label: "Resolved (24h)", value: resolved24h, color: "#16a34a", bg: "#f0fdf4" },
  ];
  return (
    <div style={{ display: "flex", gap: "0.625rem" }}>
      {items.map(({ label, value, color, bg }) => (
        <div
          key={label}
          style={{
            flex: 1,
            background: bg,
            border: `1px solid ${color}30`,
            borderTop: `2px solid ${color}`,
            borderRadius: 8,
            padding: "0.625rem 0.875rem",
          }}
        >
          <div style={{ fontSize: "0.68rem", color: "#64748b", textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 600, marginBottom: 4 }}>
            {label}
          </div>
          <div style={{ fontSize: "1.75rem", fontWeight: 800, color, letterSpacing: "-0.04em", lineHeight: 1 }}>
            {value}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Trend chart ───────────────────────────────────────────────────────────────

interface TrendChartProps {
  points: ExceptionTrendPoint[];
  fromDate: string;
  toDate: string;
}

function buildChartData(points: ExceptionTrendPoint[], from: string, to: string) {
  // Aggregate by date + category
  const byDate: Record<string, Record<string, number>> = {};
  for (const p of points) {
    const cat = getCategory(p.type);
    if (!byDate[p.date]) byDate[p.date] = {};
    byDate[p.date][cat] = (byDate[p.date][cat] ?? 0) + p.count;
  }

  // Fill every date in range with 0 if missing
  const result: Record<string, string | number>[] = [];
  const start = new Date(from + "T00:00:00Z");
  const end = new Date(to + "T23:59:59Z");
  for (let d = new Date(start); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    const row: Record<string, string | number> = { date: iso.slice(5) }; // MM-DD
    for (const cat of Object.keys(CATEGORY_COLOR)) {
      row[cat] = byDate[iso]?.[cat] ?? 0;
    }
    result.push(row);
  }
  return result;
}

const AXIS_STYLE = { fill: "#64748b", fontSize: 11 };
const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  color: "#1e293b",
  boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
  fontSize: "0.8rem",
};

function TrendChart({ points, fromDate, toDate }: TrendChartProps) {
  if (points.length === 0) {
    return (
      <div style={{ textAlign: "center", color: "#94a3b8", fontSize: "0.8rem", padding: "2rem 0" }}>
        No data for selected range
      </div>
    );
  }

  const data = buildChartData(points, fromDate, toDate);
  const categories = Object.keys(CATEGORY_COLOR);

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} barCategoryGap="25%">
        <CartesianGrid vertical={false} stroke="#f1f5f9" />
        <XAxis dataKey="date" tick={AXIS_STYLE} tickLine={false} axisLine={false} interval="preserveStartEnd" />
        <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} allowDecimals={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f8fafc" }} />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: "0.75rem", color: "#64748b" }} />
        {categories.map((cat) => (
          <Bar key={cat} dataKey={cat} stackId="a" fill={CATEGORY_COLOR[cat]} radius={cat === "System" ? [3, 3, 0, 0] : undefined} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Individual exception row ──────────────────────────────────────────────────

function ExceptionItem({ exc, onAction }: { exc: ExceptionRecord; onAction: () => void }) {
  const [busy, setBusy] = useState(false);
  const ctx = exc.context_json ?? {};
  const isOpen = exc.status === "open";

  async function doResolve() {
    setBusy(true);
    try { await resolveException({ exception_id: exc.id, note: "operator resolved" }); onAction(); }
    catch { /* surfaced in parent */ }
    setBusy(false);
  }

  async function doIgnore() {
    setBusy(true);
    try { await ignoreException({ exception_id: exc.id }); onAction(); }
    catch { /* surfaced in parent */ }
    setBusy(false);
  }

  return (
    <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 6, padding: "0.5rem 0.875rem", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        <span style={{ fontSize: "0.75rem", color: "#374151", fontFamily: "monospace", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {exc.entity_id ?? exc.id.slice(0, 12) + "…"}
        </span>
        <span style={{ fontSize: "0.68rem", color: "#94a3b8", whiteSpace: "nowrap" }}>
          {exc.created_at ? timeAgo(exc.created_at) : "—"}
        </span>
        {isOpen ? (
          <div style={{ display: "flex", gap: 4 }}>
            <ActionBtn label="Resolve" onClick={doResolve} color="#16a34a" disabled={busy} />
            <ActionBtn label="Ignore" onClick={doIgnore} color="#64748b" disabled={busy} />
          </div>
        ) : (
          <span style={{ fontSize: "0.68rem", color: "#94a3b8", fontStyle: "italic" }}>{exc.status}</span>
        )}
      </div>
      {Object.keys(ctx).length > 0 && (
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          {Object.entries(ctx).slice(0, 4).map(([k, v]) => (
            <span key={k} style={{ fontSize: "0.68rem", color: "#64748b" }}>
              <span style={{ color: "#94a3b8" }}>{k}: </span>
              <span style={{ fontFamily: "monospace", color: "#374151" }}>{String(v).slice(0, 40)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Grouped exception card ────────────────────────────────────────────────────

function ExceptionGroupCard({ group, exceptions, statusFilter, onRefresh }: {
  group: ExceptionGroup;
  exceptions: ExceptionRecord[];
  statusFilter: "open" | "resolved" | "ignored";
  onRefresh: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const sev = severityStyle(group.severity);
  const category = getCategory(group.type);
  const catColor = CATEGORY_COLOR[category] ?? "#64748b";
  const isOpen = statusFilter === "open";

  async function doBulkIgnore() {
    if (!confirm(`Ignore all ${group.count} open "${group.type}" exceptions?`)) return;
    setBusy(true);
    try { await bulkIgnoreExceptions({ type: group.type, note: "bulk ignored by operator" }); onRefresh(); }
    catch { /* surfaced in parent */ }
    setBusy(false);
  }

  const label = group.type.replace(/_/g, " ");

  return (
    <div
      style={{ background: sev.bg, border: `1px solid ${sev.border}`, borderLeft: `3px solid ${sev.accent}`, borderRadius: 10, overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,0.04)", transition: "box-shadow 0.15s" }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.boxShadow = "0 4px 12px rgba(0,0,0,0.08)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.boxShadow = "0 1px 3px rgba(0,0,0,0.04)"; }}
    >
      <div
        style={{ display: "flex", alignItems: "center", gap: "0.625rem", padding: "0.625rem 0.875rem", cursor: exceptions.length > 0 ? "pointer" : "default", flexWrap: "wrap" }}
        onClick={() => exceptions.length > 0 && setExpanded((v) => !v)}
      >
        <Chip label={group.severity} color={sev.text} bg={sev.badgeBg} />
        <Chip label={category} color={catColor} bg={`${catColor}14`} />
        <code style={{ fontSize: "0.82rem", fontWeight: 700, color: sev.text, fontFamily: "monospace", flex: 1, minWidth: 0 }}>
          {label}
        </code>
        {group.count > 1 && (
          <span style={{ background: sev.accent, color: "#fff", fontSize: "0.68rem", fontWeight: 800, padding: "1px 8px", borderRadius: 9999 }}>
            {group.count} occurrences
          </span>
        )}
        {isOpen && group.count > 1 && (
          <ActionBtn label={`Ignore all ${group.count}`} onClick={doBulkIgnore} color="#64748b" disabled={busy} />
        )}
        {exceptions.length > 0 && (
          <span style={{ color: "#94a3b8", fontSize: "0.75rem", flexShrink: 0 }}>{expanded ? "▲" : "▼"}</span>
        )}
      </div>

      {expanded && exceptions.length > 0 && (
        <div
          style={{ borderTop: `1px solid ${sev.border}`, padding: "0.625rem 0.875rem", display: "flex", flexDirection: "column", gap: "0.375rem" }}
          onClick={(e) => e.stopPropagation()}
        >
          {exceptions.slice(0, 20).map((exc) => (
            <ExceptionItem key={exc.id} exc={exc} onAction={onRefresh} />
          ))}
          {exceptions.length > 20 && (
            <p style={{ fontSize: "0.72rem", color: "#94a3b8", margin: 0, padding: "0.25rem 0" }}>
              + {exceptions.length - 20} more — use bulk ignore to clear all
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

type SevFilter = "all" | "critical" | "warning";

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "0.3rem 0.875rem",
        borderRadius: 6,
        border: "1px solid",
        fontSize: "0.78rem",
        fontWeight: 600,
        cursor: "pointer",
        background: active ? "#1e293b" : "#ffffff",
        color: active ? "#f8fafc" : "#64748b",
        borderColor: active ? "#1e293b" : "#e2e8f0",
        transition: "all 0.15s",
        textTransform: "capitalize" as const,
      }}
    >
      {children}
    </button>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

type StatusFilter = "open" | "resolved" | "ignored";

export default function ExceptionsMonitor() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [sevFilter, setSevFilter]       = useState<SevFilter>("all");
  const [typeFilter, setTypeFilter]     = useState<string>("");
  const [fromDate, setFromDate]         = useState(todayISO());
  const [toDate, setToDate]             = useState(todayISO());
  const [useDateFilter, setUseDateFilter] = useState(false);

  const [exceptions, setExceptions]     = useState<ExceptionRecord[]>([]);
  const [groups, setGroups]             = useState<ExceptionGroup[]>([]);
  const [total, setTotal]               = useState(0);
  const [todayCount, setTodayCount]     = useState(0);
  const [openCount, setOpenCount]       = useState(0);
  const [resolved24h, setResolved24h]   = useState(0);

  const [trendPoints, setTrendPoints]   = useState<ExceptionTrendPoint[]>([]);
  const [trendFrom, setTrendFrom]       = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 29);
    return d.toISOString().slice(0, 10);
  });
  const [trendTo, setTrendTo]           = useState(todayISO());

  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState<string | null>(null);

  // Fetch exceptions list
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const opts: Parameters<typeof fetchExceptions>[0] = {
        status: statusFilter,
        severity: sevFilter === "all" ? undefined : sevFilter,
        type: typeFilter || undefined,
        limit: 200,
      };
      if (useDateFilter) {
        opts.from_date = fromDate ? fromDate + "T00:00:00Z" : undefined;
        opts.to_date   = toDate   ? toDate   + "T23:59:59Z" : undefined;
      }
      const res = await fetchExceptions(opts);
      setExceptions(res.exceptions);
      setGroups(res.groups ?? []);
      setTotal(res.total);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [statusFilter, sevFilter, typeFilter, fromDate, toDate, useDateFilter]);

  // Fetch summary counts (always unfiltered by date / type)
  const loadSummary = useCallback(async () => {
    try {
      const [todayRes, openRes, resolvedRes] = await Promise.all([
        fetchExceptions({ from_date: todayISO() + "T00:00:00Z", limit: 1 }),
        fetchExceptions({ status: "open", limit: 1 }),
        fetchExceptions({ status: "resolved", from_date: (() => { const d = new Date(); d.setHours(d.getHours() - 24); return d.toISOString(); })(), limit: 1 }),
      ]);
      setTodayCount(todayRes.total);
      setOpenCount(openRes.total);
      setResolved24h(resolvedRes.total);
    } catch { /* non-fatal */ }
  }, []);

  // Fetch trend data
  const loadTrend = useCallback(async () => {
    try {
      const res = await fetchExceptionTrend({ from_date: trendFrom, to_date: trendTo });
      setTrendPoints(res.points);
    } catch { /* non-fatal */ }
  }, [trendFrom, trendTo]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { loadTrend(); }, [loadTrend]);

  const byType = new Map<string, ExceptionRecord[]>();
  for (const exc of exceptions) {
    if (!byType.has(exc.type)) byType.set(exc.type, []);
    byType.get(exc.type)!.push(exc);
  }

  const visibleGroups = groups.filter((g) =>
    sevFilter === "all" || g.severity === sevFilter
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>

      {/* ── Metrics summary ────────────────────────────────────────────────── */}
      <MetricsSummary today={todayCount} open={openCount} resolved24h={resolved24h} />

      {/* ── Trend chart ────────────────────────────────────────────────────── */}
      <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "0.875rem 1rem" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.5rem" }}>
          <span style={{ fontSize: "0.78rem", fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Exceptions Trend
          </span>
          <DateRangePicker
            fromDate={trendFrom}
            toDate={trendTo}
            onFromChange={setTrendFrom}
            onToChange={setTrendTo}
          />
        </div>
        <TrendChart points={trendPoints} fromDate={trendFrom} toDate={trendTo} />
      </div>

      {/* ── Filters ────────────────────────────────────────────────────────── */}
      <div
        style={{
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: 10,
          padding: "0.75rem 1rem",
          display: "flex",
          flexWrap: "wrap",
          gap: "0.75rem",
          alignItems: "center",
        }}
      >
        {/* Status tabs */}
        <div style={{ display: "flex", gap: "0.375rem" }}>
          {(["open", "resolved", "ignored"] as StatusFilter[]).map((t) => (
            <TabBtn key={t} active={statusFilter === t} onClick={() => setStatusFilter(t)}>{t}</TabBtn>
          ))}
        </div>

        <div style={{ width: 1, height: 24, background: "#e2e8f0" }} />

        {/* Severity filter */}
        <div style={{ display: "flex", gap: "0.375rem" }}>
          {(["all", "critical", "warning"] as SevFilter[]).map((v) => (
            <TabBtn key={v} active={sevFilter === v} onClick={() => setSevFilter(v)}>{v}</TabBtn>
          ))}
        </div>

        <div style={{ width: 1, height: 24, background: "#e2e8f0" }} />

        {/* Type filter */}
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          style={{
            border: "1px solid #e2e8f0",
            borderRadius: 6,
            background: "#ffffff",
            color: typeFilter ? "#1e293b" : "#94a3b8",
            fontSize: "0.78rem",
            fontWeight: 600,
            padding: "0.3rem 0.625rem",
            cursor: "pointer",
            outline: "none",
          }}
        >
          <option value="">All types</option>
          {ALL_TYPES.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
          ))}
        </select>

        {/* Date range toggle */}
        <label style={{ display: "flex", alignItems: "center", gap: "0.375rem", fontSize: "0.78rem", color: "#64748b", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={useDateFilter}
            onChange={(e) => setUseDateFilter(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
          Filter by date
        </label>

        {useDateFilter && (
          <DateRangePicker
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={setFromDate}
            onToChange={setToDate}
          />
        )}

        <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "#94a3b8" }}>
          {visibleGroups.length} type{visibleGroups.length !== 1 ? "s" : ""} · {total} total
        </span>
      </div>

      {/* ── States ─────────────────────────────────────────────────────────── */}
      {loading && <div style={{ color: "#64748b", fontSize: "0.875rem", padding: "1rem 0" }}>Loading…</div>}
      {error && (
        <div style={{ background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, padding: "0.75rem 1rem", color: "#dc2626", fontSize: "0.875rem" }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Empty state ─────────────────────────────────────────────────────── */}
      {!loading && !error && visibleGroups.length === 0 && (
        <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 10, padding: "2.5rem 2rem", textAlign: "center" }}>
          <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>🎉</div>
          <div style={{ fontSize: "1rem", fontWeight: 700, color: "#15803d", marginBottom: "0.25rem" }}>No actionable exceptions</div>
          <div style={{ fontSize: "0.82rem", color: "#4ade80" }}>
            {statusFilter === "open" ? "Queue is clean — no operator attention required." : `No ${statusFilter} exceptions found.`}
          </div>
        </div>
      )}

      {/* ── Grouped list ────────────────────────────────────────────────────── */}
      {!loading && !error && visibleGroups.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {visibleGroups.map((group) => (
            <ExceptionGroupCard
              key={group.type}
              group={group}
              exceptions={byType.get(group.type) ?? []}
              statusFilter={statusFilter}
              onRefresh={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}
