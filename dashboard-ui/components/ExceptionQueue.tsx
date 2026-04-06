"use client";

/**
 * ExceptionQueue — grouped, actionable exception viewer.
 *
 * Design decisions:
 * - Exceptions are grouped by type so 134 identical rows collapse into one.
 * - Each group shows a count badge and a "Ignore all" bulk action.
 * - Individual items within a group are expandable.
 * - Warning-severity items use a blue-tinted card (informational, not alarming).
 * - Critical-severity items use a red-tinted card.
 * - Only exceptions that require operator attention should appear here.
 */

import { useEffect, useState, useCallback } from "react";
import {
  fetchExceptions,
  resolveException,
  ignoreException,
  bulkIgnoreExceptions,
} from "@/lib/api";
import type { ExceptionRecord, ExceptionGroup } from "@/types";

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

function getCategory(type: string): string {
  return CATEGORY[type] ?? "System";
}

// ── Card style per severity ───────────────────────────────────────────────────

function severityStyle(severity: string) {
  if (severity === "critical") {
    return {
      bg:      "#fef2f2",
      border:  "#fecaca",
      accent:  "#ef4444",
      text:    "#dc2626",
      badgeBg: "#fee2e2",
    };
  }
  // warning → blue-tinted (informational, not alarming)
  return {
    bg:      "#eff6ff",
    border:  "#bfdbfe",
    accent:  "#3b82f6",
    text:    "#1d4ed8",
    badgeBg: "#dbeafe",
  };
}

// ── Tiny helpers ──────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
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

// ── Action button ─────────────────────────────────────────────────────────────

function ActionBtn({
  label, onClick, color, disabled,
}: {
  label: string;
  onClick: () => void;
  color: string;
  disabled?: boolean;
}) {
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

// ── Individual exception row (inside expanded group) ─────────────────────────

function ExceptionItem({
  exc,
  onAction,
}: {
  exc: ExceptionRecord;
  onAction: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const sev = severityStyle(exc.severity);
  const ctx = exc.context_json ?? {};
  const isOpen = exc.status === "open";

  async function doResolve() {
    setBusy(true);
    try {
      await resolveException({ exception_id: exc.id, note: "operator resolved" });
      onAction();
    } catch { /* surface in parent */ }
    setBusy(false);
  }

  async function doIgnore() {
    setBusy(true);
    try {
      await ignoreException({ exception_id: exc.id });
      onAction();
    } catch { /* surface in parent */ }
    setBusy(false);
  }

  return (
    <div
      style={{
        background: "#ffffff",
        border: "1px solid #e2e8f0",
        borderRadius: 6,
        padding: "0.5rem 0.875rem",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        {/* Entity */}
        <span style={{ fontSize: "0.75rem", color: "#374151", fontFamily: "monospace", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {exc.entity_id ?? exc.id.slice(0, 12) + "…"}
        </span>

        {/* Timestamp */}
        <span style={{ fontSize: "0.68rem", color: "#94a3b8", whiteSpace: "nowrap" }}>
          {exc.created_at ? timeAgo(exc.created_at) : "—"}
        </span>

        {/* Actions */}
        {isOpen && (
          <div style={{ display: "flex", gap: 4 }}>
            <ActionBtn label="Resolve" onClick={doResolve} color="#16a34a" disabled={busy} />
            <ActionBtn label="Ignore" onClick={doIgnore} color="#64748b" disabled={busy} />
          </div>
        )}
        {!isOpen && (
          <span style={{ fontSize: "0.68rem", color: "#94a3b8", fontStyle: "italic" }}>
            {exc.status}
          </span>
        )}
      </div>

      {/* Context key-values */}
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

function ExceptionGroupCard({
  group,
  exceptions,
  statusFilter,
  onRefresh,
}: {
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
    try {
      await bulkIgnoreExceptions({ type: group.type, note: "bulk ignored by operator" });
      onRefresh();
    } catch { /* surface in parent */ }
    setBusy(false);
  }

  const label = group.type.replace(/_/g, " ");

  return (
    <div
      style={{
        background: sev.bg,
        border: `1px solid ${sev.border}`,
        borderLeft: `3px solid ${sev.accent}`,
        borderRadius: 10,
        overflow: "hidden",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
        transition: "box-shadow 0.15s",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.boxShadow = "0 4px 12px rgba(0,0,0,0.08)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.boxShadow = "0 1px 3px rgba(0,0,0,0.04)"; }}
    >
      {/* Header row — always visible */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.625rem",
          padding: "0.625rem 0.875rem",
          cursor: exceptions.length > 0 ? "pointer" : "default",
          flexWrap: "wrap",
        }}
        onClick={() => exceptions.length > 0 && setExpanded((v) => !v)}
      >
        {/* Severity chip */}
        <Chip
          label={group.severity}
          color={sev.text}
          bg={sev.badgeBg}
        />

        {/* Category chip */}
        <Chip label={category} color={catColor} bg={`${catColor}14`} />

        {/* Exception type */}
        <code style={{ fontSize: "0.82rem", fontWeight: 700, color: sev.text, fontFamily: "monospace", flex: 1, minWidth: 0 }}>
          {label}
        </code>

        {/* Count badge */}
        {group.count > 1 && (
          <span
            style={{
              background: sev.accent,
              color: "#fff",
              fontSize: "0.68rem",
              fontWeight: 800,
              padding: "1px 8px",
              borderRadius: 9999,
              letterSpacing: "0.02em",
            }}
          >
            {group.count} occurrences
          </span>
        )}

        {/* Bulk ignore */}
        {isOpen && group.count > 1 && (
          <ActionBtn
            label={`Ignore all ${group.count}`}
            onClick={doBulkIgnore}
            color="#64748b"
            disabled={busy}
          />
        )}

        {/* Expand arrow */}
        {exceptions.length > 0 && (
          <span style={{ color: "#94a3b8", fontSize: "0.75rem", flexShrink: 0 }}>
            {expanded ? "▲" : "▼"}
          </span>
        )}
      </div>

      {/* Expanded items */}
      {expanded && exceptions.length > 0 && (
        <div
          style={{
            borderTop: `1px solid ${sev.border}`,
            padding: "0.625rem 0.875rem",
            display: "flex",
            flexDirection: "column",
            gap: "0.375rem",
          }}
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

// ── Filters bar ───────────────────────────────────────────────────────────────

type SevFilter = "all" | "critical" | "warning";

function FilterBar({
  sevFilter,
  onSev,
  total,
  groups,
}: {
  sevFilter: SevFilter;
  onSev: (s: SevFilter) => void;
  total: number;
  groups: ExceptionGroup[];
}) {
  const SEV_OPTIONS: { value: SevFilter; label: string }[] = [
    { value: "all",      label: `All (${total})` },
    { value: "critical", label: `Critical (${groups.filter((g) => g.severity === "critical").reduce((s, g) => s + g.count, 0)})` },
    { value: "warning",  label: `Warning (${groups.filter((g) => g.severity === "warning").reduce((s, g) => s + g.count, 0)})` },
  ];

  return (
    <div style={{ display: "flex", gap: "0.375rem" }}>
      {SEV_OPTIONS.map(({ value, label }) => (
        <button
          key={value}
          onClick={() => onSev(value)}
          style={{
            padding: "0.3rem 0.875rem",
            borderRadius: 6,
            border: "1px solid",
            fontSize: "0.78rem",
            fontWeight: 600,
            cursor: "pointer",
            background: sevFilter === value ? "#1e293b" : "#ffffff",
            color: sevFilter === value ? "#f8fafc" : "#64748b",
            borderColor: sevFilter === value ? "#1e293b" : "#e2e8f0",
            transition: "all 0.15s",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

type StatusFilter = "open" | "resolved" | "ignored";

export default function ExceptionQueue() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [sevFilter, setSevFilter] = useState<SevFilter>("all");
  const [exceptions, setExceptions] = useState<ExceptionRecord[]>([]);
  const [groups, setGroups] = useState<ExceptionGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchExceptions({
        status: statusFilter,
        severity: sevFilter === "all" ? undefined : sevFilter,
        limit: 200,
      });
      if (!cancelled) {
        setExceptions(res.exceptions);
        setGroups(res.groups ?? []);
        setTotal(res.total);
      }
    } catch (e) {
      if (!cancelled) setError(String(e));
    } finally {
      if (!cancelled) setLoading(false);
    }
    return () => { cancelled = true; };
  }, [statusFilter, sevFilter]);

  useEffect(() => { load(); }, [load]);

  // Build a map: type → [exceptions] for the currently loaded slice
  const byType = new Map<string, ExceptionRecord[]>();
  for (const exc of exceptions) {
    if (!byType.has(exc.type)) byType.set(exc.type, []);
    byType.get(exc.type)!.push(exc);
  }

  // Filter groups by severity filter
  const visibleGroups = groups.filter((g) =>
    sevFilter === "all" || g.severity === sevFilter
  );

  const STATUS_TABS: StatusFilter[] = ["open", "resolved", "ignored"];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      {/* Status tabs */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
        {STATUS_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setStatusFilter(t)}
            style={{
              padding: "0.3rem 0.875rem",
              borderRadius: 6,
              border: "1px solid",
              fontSize: "0.8rem",
              fontWeight: 600,
              cursor: "pointer",
              background: statusFilter === t ? "#1e293b" : "#ffffff",
              color: statusFilter === t ? "#f8fafc" : "#64748b",
              borderColor: statusFilter === t ? "#1e293b" : "#e2e8f0",
              textTransform: "capitalize",
              transition: "all 0.15s",
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Severity filter + count */}
      {!loading && !error && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "0.5rem" }}>
          <FilterBar
            sevFilter={sevFilter}
            onSev={setSevFilter}
            total={total}
            groups={groups}
          />
          <span style={{ fontSize: "0.78rem", color: "#94a3b8" }}>
            {visibleGroups.length} type{visibleGroups.length !== 1 ? "s" : ""} · {total} total
          </span>
        </div>
      )}

      {/* States */}
      {loading && (
        <div style={{ color: "#64748b", fontSize: "0.875rem", padding: "1rem 0" }}>
          Loading…
        </div>
      )}
      {error && (
        <div style={{
          background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8,
          padding: "0.75rem 1rem", color: "#dc2626", fontSize: "0.875rem",
        }}>
          ⚠ {error}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && visibleGroups.length === 0 && (
        <div
          style={{
            background: "#f0fdf4",
            border: "1px solid #bbf7d0",
            borderRadius: 10,
            padding: "2.5rem 2rem",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>🎉</div>
          <div style={{ fontSize: "1rem", fontWeight: 700, color: "#15803d", marginBottom: "0.25rem" }}>
            No actionable exceptions
          </div>
          <div style={{ fontSize: "0.82rem", color: "#4ade80" }}>
            {statusFilter === "open"
              ? "Queue is clean — no operator attention required."
              : `No ${statusFilter} exceptions found.`}
          </div>
        </div>
      )}

      {/* Grouped list */}
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
