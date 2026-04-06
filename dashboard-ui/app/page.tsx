/**
 * Home page — health tiles, live activity feed, navigation hub.
 * Fetches health on the server; live feed runs client-side via WebSocket.
 */
import { fetchHealth } from "@/lib/api";
import HealthTiles from "@/components/HealthTiles";
import AlertBanner from "@/components/AlertBanner";
import NavigationCard from "@/components/NavigationCard";

export const revalidate = 0;

const NAV_ITEMS = [
  { href: "/activity",       title: "Live Activity",   icon: "⚡", description: "Real-time job events feed." },
  { href: "/kpis",           title: "KPIs",            icon: "📊", description: "Pickup, voicemail & enrollment rates." },
  { href: "/queue",          title: "Queue Health",    icon: "⚙️", description: "Stuck jobs & expired leases." },
  { href: "/exceptions",     title: "Exceptions",      icon: "⚠️", description: "Open exceptions needing action." },
  { href: "/alerts",         title: "Alerts",          icon: "🔔", description: "Queue lag, error spikes, worker status." },
  { href: "/ai-performance", title: "AI Performance",  icon: "🤖", description: "Intent & consent distribution." },
  { href: "/crm-health",     title: "CRM Health",      icon: "🔗", description: "GHL task & VM update rates." },
] as const;

export default async function HomePage() {
  let health = null;
  let healthError: string | null = null;
  try {
    health = await fetchHealth();
  } catch (e) {
    healthError = String(e);
  }

  return (
    <main style={{ padding: "1.5rem", maxWidth: 1100, margin: "0 auto" }}>
      <h1 style={{ marginBottom: "0.25rem" }}>Cora Dashboard</h1>
      <p style={{ color: "#64748b", fontSize: "0.875rem", marginBottom: "1.5rem" }}>
        Monitoring & operator console
      </p>

      <AlertBanner />

      {/* Health tiles */}
      {healthError ? (
        <p style={{ color: "#f87171" }}>Failed to load health: {healthError}</p>
      ) : (
        <HealthTiles health={health!} />
      )}

      {/* Navigation hub */}
      <section style={{ marginTop: "1.25rem" }}>
        <h2 style={{ marginBottom: "0.5rem", fontSize: "0.8rem", color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Sections
        </h2>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: "0.5rem",
          }}
        >
          {NAV_ITEMS.map((item) => (
            <NavigationCard key={item.href} {...item} />
          ))}
        </div>
      </section>
    </main>
  );
}
