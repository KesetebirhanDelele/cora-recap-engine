"use client";

import { useState } from "react";
import NavigationCard, { type NavCategory } from "@/components/NavigationCard";
import type { CardIndicator } from "@/lib/indicators";
import { INDICATOR_COLORS } from "@/lib/indicators";

export interface ResolvedNavItem {
  href: string;
  title: string;
  icon: string;
  description: string;
  category: NavCategory;
  badge?: number;
  badgeCritical?: boolean;
  indicator?: CardIndicator;
}

export interface ResolvedNavGroup {
  label: string;
  category: NavCategory;
  items: ResolvedNavItem[];
}

const ACCENT: Record<NavCategory, string> = {
  operations: "#f59e0b",
  analytics:  "#3b82f6",
  system:     "#8b5cf6",
};

function groupIssueCount(group: ResolvedNavGroup): { total: number; hasBadgeItems: boolean; anyCritical: boolean } {
  let total = 0;
  let hasBadgeItems = false;
  let anyCritical = false;
  for (const item of group.items) {
    if (item.badge === undefined) continue;
    hasBadgeItems = true;
    total += item.badge;
    if (item.badgeCritical && item.badge > 0) anyCritical = true;
  }
  return { total, hasBadgeItems, anyCritical };
}

export default function DashboardTabs({ groups }: { groups: ResolvedNavGroup[] }) {
  const [active, setActive] = useState(groups[0]?.category);
  const activeGroup = groups.find((g) => g.category === active) ?? groups[0];

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: "0 1.5rem 1rem" }}>
      {/* ── Persistent alert strip ─────────────────────────────────────── */}
      <div
        style={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          gap: "1.25rem",
          padding: "0.5rem 0.25rem",
        }}
      >
        {groups.map((group) => {
          const { total, hasBadgeItems, anyCritical } = groupIssueCount(group);
          const color = !hasBadgeItems
            ? "#94a3b8"
            : total === 0
            ? INDICATOR_COLORS.green
            : anyCritical
            ? INDICATOR_COLORS.red
            : INDICATOR_COLORS.yellow;
          return (
            <div
              key={group.category}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.35rem",
                fontSize: "0.78rem",
                fontWeight: 600,
                color: "#475569",
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: ACCENT[group.category],
                  display: "inline-block",
                }}
              />
              {group.label}
              {hasBadgeItems && (
                <span style={{ color, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {total} {total === 1 ? "issue" : "issues"}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Tab pills ───────────────────────────────────────────────────── */}
      <div
        style={{
          flexShrink: 0,
          display: "flex",
          gap: "0.4rem",
          padding: "0 0.25rem 0.75rem",
          borderBottom: "1px solid #e2e8f0",
        }}
      >
        {groups.map((group) => {
          const isActive = group.category === activeGroup?.category;
          const accent = ACCENT[group.category];
          return (
            <button
              key={group.category}
              onClick={() => setActive(group.category)}
              style={{
                appearance: "none",
                border: "none",
                cursor: "pointer",
                padding: "0.45rem 0.9rem",
                borderRadius: 7,
                fontSize: "0.85rem",
                fontWeight: 700,
                letterSpacing: "-0.01em",
                background: isActive ? accent : "transparent",
                color: isActive ? "#ffffff" : "#475569",
                transition: "background 0.15s ease, color 0.15s ease",
              }}
            >
              {group.label}
              <span style={{ marginLeft: "0.4rem", opacity: 0.75, fontWeight: 600 }}>
                {group.items.length}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Active group's cards ────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          paddingTop: "0.9rem",
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: "0.75rem",
          }}
        >
          {activeGroup?.items.map((item) => (
            <NavigationCard
              key={item.href}
              href={item.href}
              title={item.title}
              icon={item.icon}
              description={item.description}
              category={item.category}
              badge={item.badge}
              badgeCritical={item.badgeCritical}
              indicator={item.indicator}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
