import { fetchHealth, fetchCardMetrics, fetchWrongDates, fetchDeliveryHealth, fetchOptouts } from "@/lib/api";
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
  badgeKey?: keyof HealthResponse | "queue_issues" | "wrong_date_open" | "delivery_bad" | "optout_review";
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
      { href: "/optouts",         title: "Opt-outs & DND",      icon: "🛑", description: "Leads who asked us to stop — review, apply or undo DND in GHL.", category: "operations", badgeKey: "optout_review", badgeCritical: true },
      { href: "/delivery-health", title: "Delivery Health",     icon: "📬", description: "Email, SMS and calls: sent vs delivered, last delivery, and silence alerts.", category: "operations", badgeKey: "delivery_bad", badgeCritical: true },
      { href: "/sms-monitor",    title: "SMS Monitor",         icon: "💬", description: "Today's SMS budget (max 999 segments / Pacific day), pre-send gate verdicts and every text sent.", category: "operations" },
      { href: "/wrong-dates",    title: "Wrong Date Monitor",  icon: "📆", description: "Leads sent a wrong class-start / open-house date — send a correction.", category: "operations", badgeKey: "wrong_date_open", badgeCritical: true },
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

function getBadge(item: NavItem, health: HealthResponse, wrongDateOpen: number, deliveryBad: number, optoutReview: number): number | undefined {
  if (!item.badgeKey) return undefined;
  if (item.badgeKey === "optout_review") return optoutReview > 0 ? optoutReview : undefined;
  if (item.badgeKey === "delivery_bad") return deliveryBad > 0 ? deliveryBad : undefined;
  if (item.badgeKey === "wrong_date_open") return wrongDateOpen > 0 ? wrongDateOpen : undefined;
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
  wrongDateOpen: number,
  wrongDateClosed24h: number | null,
  deliverySummary: string | null,
  deliveryBad: number,
  optoutReview: number,
): ResolvedNavGroup[] {
  return NAV_GROUPS.map((group) => ({
    label: group.label,
    category: group.category,
    items: group.items.map((item) => ({
      href: item.href,
      title: item.title,
      icon: item.icon,
      description:
        item.badgeKey === "wrong_date_open" && wrongDateClosed24h !== null
          ? `Open ${wrongDateOpen} · Closed ${wrongDateClosed24h} in the last 24h. Wrong class / open-house dates sent to leads.`
          : item.badgeKey === "delivery_bad" && deliverySummary
            ? deliverySummary
            : item.description,
      category: item.category,
      badge: health ? getBadge(item, health, wrongDateOpen, deliveryBad, optoutReview) : undefined,
      badgeCritical: item.badgeCritical,
      indicator: computeIndicator(item.href, cardMetrics),
    })),
  }));
}

export default async function HomePage() {
  let health: HealthResponse | null = null;
  let healthError: string | null = null;
  let cardMetrics: CardMetricsResponse | null = null;
  let wrongDateOpen = 0;
  let wrongDateClosed24h: number | null = null;
  let deliverySummary: string | null = null;
  let deliveryBad = 0;
  let optoutReview = 0;
  try {
    optoutReview = (await fetchOptouts()).counts.review ?? 0;
  } catch { /* tile still renders without a badge */ }
  try {
    const dh = await fetchDeliveryHealth();
    deliveryBad = dh.channels.filter((c) => c.level === "red").length;
    deliverySummary = dh.channels
      .map((c) => `${c.label} ${c.rate === null ? "—" : Math.round(c.rate * 100) + "%"}`)
      .join(" · ") + " delivered (24h)" + (deliveryBad ? ` · ${deliveryBad} channel(s) RED` : "");
  } catch { /* tile still renders without a summary */ }
  try {
    const wd = await fetchWrongDates("open");
    wrongDateOpen = wd.open_count;
    wrongDateClosed24h = wd.stats.closed_24h;
  } catch { /* tile still renders without a badge */ }
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
      <DashboardSections groups={resolveGroups(health, cardMetrics, wrongDateOpen, wrongDateClosed24h, deliverySummary, deliveryBad, optoutReview)} />
    </div>
  );
}
