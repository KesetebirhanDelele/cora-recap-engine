"use client";

import Link from "next/link";

export type NavCategory = "operations" | "analytics" | "system";

interface Props {
  href: string;
  title: string;
  description: string;
  icon: string;
  category: NavCategory;
  badge?: number;
  badgeCritical?: boolean;
}

const ACCENT: Record<NavCategory, string> = {
  operations: "#f59e0b",
  analytics:  "#3b82f6",
  system:     "#8b5cf6",
};

export default function NavigationCard({
  href, title, description, icon, category, badge, badgeCritical,
}: Props) {
  const accent = ACCENT[category];

  return (
    <Link href={href} style={{ textDecoration: "none", display: "block", height: "100%" }}>
      <div
        style={{
          position: "relative",
          height: "100%",
          boxSizing: "border-box",
          background: "#ffffff",
          border: "1px solid #e2e8f0",
          borderLeft: `3px solid ${accent}`,
          borderRadius: 8,
          padding: "0.5rem 0.75rem",
          display: "flex",
          flexDirection: "column",
          gap: "0.25rem",
          cursor: "pointer",
          transition: "transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease",
          boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        }}
        onMouseEnter={(e) => {
          const el = e.currentTarget as HTMLDivElement;
          el.style.transform = "scale(1.02)";
          el.style.boxShadow = `0 6px 20px rgba(0,0,0,0.1), 0 0 0 1px ${accent}44`;
          el.style.borderColor = accent;
        }}
        onMouseLeave={(e) => {
          const el = e.currentTarget as HTMLDivElement;
          el.style.transform = "scale(1)";
          el.style.boxShadow = "0 1px 3px rgba(0,0,0,0.05)";
          el.style.borderColor = "#e2e8f0";
        }}
      >
        {/* Badge */}
        {badge !== undefined && badge > 0 && (
          <div
            style={{
              position: "absolute",
              top: 6,
              right: 6,
              minWidth: 18,
              height: 18,
              borderRadius: 9,
              background: badgeCritical ? "#dc2626" : "#d97706",
              color: "#fff",
              fontSize: "0.62rem",
              fontWeight: 800,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "0 5px",
              boxShadow: badgeCritical ? "0 2px 6px #dc262644" : "0 2px 6px #d9770644",
              letterSpacing: "0.02em",
            }}
          >
            {badge > 99 ? "99+" : badge}
          </div>
        )}

        {/* Icon */}
        <span style={{ fontSize: "1rem", lineHeight: 1, flexShrink: 0 }}>{icon}</span>

        {/* Title */}
        <div
          style={{
            fontSize: "0.875rem",
            fontWeight: 700,
            color: "#0f172a",
            lineHeight: 1.25,
            letterSpacing: "-0.01em",
          }}
        >
          {title}
        </div>

        {/* Description */}
        <div
          style={{
            fontSize: "0.82rem",
            fontWeight: 500,
            color: "#374151",
            lineHeight: 1.4,
            marginTop: "auto",
          }}
        >
          {description}
        </div>
      </div>
    </Link>
  );
}
