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

export default function DashboardSections({ groups }: { groups: ResolvedNavGroup[] }) {
  return (
    <div style={{ padding: "0.5rem 1.5rem 2rem", display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {groups.map((group) => {
        const { total, hasBadgeItems, anyCritical } = groupIssueCount(group);
        const accent = ACCENT[group.category];
        const issueColor = !hasBadgeItems
          ? "#94a3b8"
          : total === 0
          ? INDICATOR_COLORS.green
          : anyCritical
          ? INDICATOR_COLORS.red
          : INDICATOR_COLORS.yellow;

        return (
          <section key={group.category}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                marginBottom: "0.6rem",
              }}
            >
              <span
                style={{
                  width: 4,
                  height: 14,
                  borderRadius: 2,
                  background: accent,
                  display: "inline-block",
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  fontSize: "0.82rem",
                  fontWeight: 700,
                  color: "#334155",
                  textTransform: "uppercase" as const,
                  letterSpacing: "0.07em",
                }}
              >
                {group.label}
              </span>
              {hasBadgeItems && (
                <span
                  style={{
                    fontSize: "0.78rem",
                    fontWeight: 700,
                    color: issueColor,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {total} {total === 1 ? "issue" : "issues"}
                </span>
              )}
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
                gap: "0.75rem",
              }}
            >
              {group.items.map((item) => (
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
          </section>
        );
      })}
    </div>
  );
}
