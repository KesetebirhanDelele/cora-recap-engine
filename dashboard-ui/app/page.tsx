import { fetchHealth } from "@/lib/api";
import HealthTiles from "@/components/HealthTiles";
import AlertBanner from "@/components/AlertBanner";
import NavigationCard, { type NavCategory } from "@/components/NavigationCard";
import type { HealthResponse } from "@/types";

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
    label: "Operations",
    category: "operations",
    items: [
      { href: "/activity",    title: "Live Activity", icon: "⚡", description: "Real-time stream of job events.", category: "operations", badgeKey: "jobs_completed_last_5m" },
      { href: "/exceptions",  title: "Exceptions Monitor", icon: "⚠️", description: "Real-time issue queue.", category: "operations", badgeKey: "open_exception_count", badgeCritical: true },
      { href: "/queue",       title: "Queue Health",  icon: "⚙️", description: "Stuck jobs & expired leases.", category: "operations", badgeKey: "queue_issues" },
      { href: "/alerts",      title: "Alerts",        icon: "🔔", description: "Lag, error, and worker alerts.", category: "operations" },
    ],
  },
  {
    label: "Analytics",
    category: "analytics",
    items: [
      { href: "/voice-performance",  title: "Voice Performance",  icon: "🎙️", description: "Trends, WoW & call efficiency.", category: "analytics" },
      { href: "/ai-performance",     title: "AI Performance",     icon: "🤖", description: "Intent, consent & AI quality.", category: "analytics" },
      { href: "/conversion-funnel",  title: "Conversion Funnel",  icon: "📉", description: "Calls → pickup → engagement → booking.", category: "analytics" },
    ],
  },
  {
    label: "System",
    category: "system",
    items: [
      { href: "/crm-health",        title: "CRM Health",       icon: "🔗", description: "GHL task & VM update rates.",       category: "system" },
      { href: "/system-anomalies",  title: "System Anomalies", icon: "📊", description: "Spikes & unusual patterns.",         category: "system" },
    ],
  },
];

const ACCENT: Record<NavCategory, string> = {
  operations: "#f59e0b",
  analytics:  "#3b82f6",
  system:     "#8b5cf6",
};

function getBadge(item: NavItem, health: HealthResponse): number | undefined {
  if (!item.badgeKey) return undefined;
  if (item.badgeKey === "queue_issues") {
    const n = health.stuck_job_count + health.expired_lease_count;
    return n > 0 ? n : undefined;
  }
  const v = health[item.badgeKey as keyof HealthResponse];
  return typeof v === "number" && v > 0 ? v : undefined;
}

export default async function HomePage() {
  let health: HealthResponse | null = null;
  let healthError: string | null = null;
  try {
    health = await fetchHealth();
  } catch (e) {
    healthError = String(e);
  }

  const recordedAt = health?.recorded_at
    ? new Date(health.recorded_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div
      style={{
        height: "100vh",
        overflow: "hidden",
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

        {/* Right side: shadow pill + timestamp */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {recordedAt && (
            <span style={{ fontSize: "0.8rem", color: "#64748b" }}>
              Updated {recordedAt}
            </span>
          )}
          {health?.shadow_mode_enabled && (
            <span
              style={{
                fontSize: "0.75rem",
                fontWeight: 700,
                textTransform: "uppercase" as const,
                letterSpacing: "0.08em",
                color: "#d97706",
                background: "#fffbeb",
                border: "1px solid #fde68a",
                borderRadius: 6,
                padding: "3px 10px",
              }}
            >
              ◉ Shadow Mode
            </span>
          )}
          {!health?.shadow_mode_enabled && health && (
            <span
              style={{
                fontSize: "0.75rem",
                fontWeight: 700,
                textTransform: "uppercase" as const,
                letterSpacing: "0.08em",
                color: "#16a34a",
                background: "#f0fdf4",
                border: "1px solid #bbf7d0",
                borderRadius: 6,
                padding: "3px 10px",
              }}
            >
              ◉ Live
            </span>
          )}
        </div>
      </header>

      {/* ── Alert strip (zero height when empty) ────────────────────────── */}
      <div style={{ flexShrink: 0, padding: "0 1.5rem" }}>
        <AlertBanner />
      </div>

      {/* ── System Health ────────────────────────────────────────────────── */}
      <div
        style={{
          flexShrink: 0,
          margin: "0.625rem 1.5rem",
          background: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          padding: "0.875rem 1.125rem",
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        }}
      >
        {healthError ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#dc2626", fontSize: "0.8rem" }}>
            <span>⚠</span> Health data unavailable — {healthError}
          </div>
        ) : (
          <HealthTiles health={health!} />
        )}
      </div>

      {/* ── Navigation groups ────────────────────────────────────────────── */}
      <div
        style={{
          flexShrink: 0,
          height: 200,
          padding: "0 1.5rem 0.875rem",
          display: "grid",
          gridTemplateColumns: "4fr 3fr 2fr",
          gap: "0.75rem",
          overflow: "hidden",
        }}
      >
        {NAV_GROUPS.map((group) => (
          <section
            key={group.label}
            style={{
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              overflow: "hidden",
            }}
          >
            {/* Group label */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                marginBottom: "0.5rem",
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  width: 3,
                  height: 12,
                  borderRadius: 2,
                  background: ACCENT[group.category],
                  display: "inline-block",
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  fontSize: "0.78rem",
                  fontWeight: 700,
                  color: "#64748b",
                  textTransform: "uppercase" as const,
                  letterSpacing: "0.07em",
                }}
              >
                {group.label}
              </span>
            </div>

            {/* Cards grid */}
            <div
              style={{
                flex: 1,
                minHeight: 0,
                display: "grid",
                gridTemplateColumns: `repeat(${group.items.length}, 1fr)`,
                gap: "0.5rem",
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
                  badge={health ? getBadge(item, health) : undefined}
                  badgeCritical={item.badgeCritical}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
