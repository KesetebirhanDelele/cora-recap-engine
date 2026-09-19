import { fetchHealth, fetchCardMetrics } from "@/lib/api";
import SystemStatusBar from "@/components/SystemStatusBar";
import { type NavCategory } from "@/components/NavigationCard";
import DashboardSections, { type ResolvedNavGroup } from "@/components/DashboardSections";
import { computeIndicator } from "@/lib/indicators";
import type { HealthResponse, CardMetricsResponse } from "@/types";

export const revalidate = 0;

interface NavItem {
  href: string;
  title: string;
  icon: string;
  description: string;
  category: NavCategory;
  badgeKey?: keyof HealthResponse | "queue_issues";
  badgeCritical?: boolean;
}

const NAV_GROUPS: { label: string; category: NavCategory; items: NavItem[] }[] = [
  {
    label: "Analytics",
    category: "analytics",
    items: [
      { href: "/voice-performance-v2", title: "Voice Performance", icon: "🎙️", description: "Date-filtered trends, WoW & call efficiency.", category: "analytics" },
      { href: "/engagement-analysis",  title: "Engagement Analysis", icon: "🤖", description: "Intent, consent & engagement metrics.", category: "analytics" },
      { href: "/conversion-funnel",   title: "Sales Queue",         icon: "📞", description: "Priority-ranked calls with scoring, recording & outcome logging.", category: "analytics" },
      { href: "/campaign-overview",   title: "Scheduled Actions",   icon: "📅", description: "Upcoming scheduled actions by date window.", category: "analytics" },
      { href: "/staff-call-quality",  title: "Staff Call Quality",  icon: "🎧", description: "Sales-rep & support-staff calls, transcribed & scored.", category: "analytics" },
      { href: "/ai-cold-lead-tagging", title: "AI Cold Lead Tagging", icon: "🏷️", description: "Daily tagging of stale leads — activity & backlog.", category: "analytics" },
    ],
  },
  {
    label: "Operations",
    category: "operations",
    items: [
      { href: "/activity",        title: "Live Activity",       icon: "⚡",  description: "Real-time stream of job events.", category: "operations", badgeKey: "jobs_completed_last_5m" },
      { href: "/exceptions",      title: "Exceptions Monitor",  icon: "⚠️",  description: "Real-time issue queue.", category: "operations", badgeKey: "open_exception_count", badgeCritical: true },
      { href: "/queue",           title: "Queue Health",        icon: "⚙️",  description: "Stuck jobs & expired leases.", category: "operations", badgeKey: "queue_issues" },
      { href: "/alerts",          title: "Alerts",              icon: "🔔", description: "Lag, error, and worker alerts.", category: "operations" },
      { href: "/contact-lookup",  title: "Contact Drill-Down",  icon: "🔍", description: "Inspect all data for a single contact.", category: "operations" },
      { href: "/lead-lifecycle",  title: "Lead Lifecycle",      icon: "🗺️", description: "Per-lead journey: campaign, VM tier, touchpoints & finalization.", category: "operations" },
      { href: "/settings",        title: "Settings",            icon: "⚙", description: "Calling windows, delays & brand identity.", category: "operations" },
      { href: "/db-explorer",     title: "DB Explorer",         icon: "🗄️", description: "Browse tables and run SQL queries.", category: "operations" },
    ],
  },
  {
    label: "System",
    category: "system",
    items: [
      { href: "/system-controls",   title: "System Controls",  icon: "🎛️", description: "Shadow/live mode, GHL writes & system pause.", category: "system" },
      { href: "/crm-health",        title: "CRM Health",       icon: "🔗", description: "GHL task & VM update rates.",       category: "system" },
      { href: "/system-anomalies",  title: "System Anomalies", icon: "📊", description: "Spikes & unusual patterns.",         category: "system" },
    ],
  },
];

function getBadge(item: NavItem, health: HealthResponse): number | undefined {
  if (!item.badgeKey) return undefined;
  if (item.badgeKey === "queue_issues") {
    const n = health.stuck_job_count + health.expired_lease_count;
    return n > 0 ? n : undefined;
  }
  const v = health[item.badgeKey as keyof HealthResponse];
  return typeof v === "number" && v > 0 ? v : undefined;
}

function resolveGroups(
  health: HealthResponse | null,
  cardMetrics: CardMetricsResponse | null,
): ResolvedNavGroup[] {
  return NAV_GROUPS.map((group) => ({
    label: group.label,
    category: group.category,
    items: group.items.map((item) => ({
      href: item.href,
      title: item.title,
      icon: item.icon,
      description: item.description,
      category: item.category,
      badge: health ? getBadge(item, health) : undefined,
      badgeCritical: item.badgeCritical,
      indicator: computeIndicator(item.href, cardMetrics),
    })),
  }));
}

export default async function HomePage() {
  let health: HealthResponse | null = null;
  let healthError: string | null = null;
  let cardMetrics: CardMetricsResponse | null = null;
  try {
    [health, cardMetrics] = await Promise.all([fetchHealth(), fetchCardMetrics()]);
  } catch (e) {
    healthError = String(e);
    // Attempt health independently if card metrics failed
    if (!health) {
      try { health = await fetchHealth(); } catch { /* ignore */ }
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        background: "#f1f5f9",
        fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        color: "#1e293b",
        WebkitFontSmoothing: "antialiased",
      } as React.CSSProperties}
    >
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header
        style={{
          flexShrink: 0,
          padding: "1rem 1.5rem 0.875rem",
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
        }}
      >
        <div>
          <h1
            style={{
              margin: 0,
              fontSize: "1.6rem",
              fontWeight: 800,
              color: "#0f172a",
              letterSpacing: "-0.03em",
              lineHeight: 1.1,
            }}
          >
            Dashboard
          </h1>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
            Monitoring &amp; operator console · Cora Voice AI
          </p>
        </div>

      </header>

      {/* ── Status bar (Option A: status strip + Option B: alert rows) ─────── */}
      <div style={{ flexShrink: 0, padding: "0.5rem 1.5rem 0" }}>
        <SystemStatusBar health={health} healthError={healthError} />
      </div>

      {/* ── Navigation (stacked sections: Analytics / Operations / System) ──── */}
      <DashboardSections groups={resolveGroups(health, cardMetrics)} />
    </div>
  );
}
