"use client";

import Link from "next/link";

interface Props {
  href: string;
  title: string;
  description: string;
  icon: string;
}

export default function NavigationCard({ href, title, description, icon }: Props) {
  return (
    <Link href={href} style={{ textDecoration: "none" }}>
      <div
        style={{
          background: "#1e293b",
          border: "1px solid #334155",
          borderRadius: 10,
          padding: "0.75rem 1rem",
          cursor: "pointer",
          transition: "border-color 0.15s, transform 0.15s",
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: "0.75rem",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLDivElement).style.borderColor = "#3b82f6";
          (e.currentTarget as HTMLDivElement).style.transform = "scale(1.02)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLDivElement).style.borderColor = "#334155";
          (e.currentTarget as HTMLDivElement).style.transform = "scale(1)";
        }}
      >
        <span style={{ fontSize: "1.4rem", flexShrink: 0 }}>{icon}</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: "bold", fontSize: "0.9rem", color: "#e2e8f0" }}>
            {title}
          </div>
          <div style={{ fontSize: "0.75rem", color: "#64748b", lineHeight: 1.3, marginTop: 2 }}>
            {description}
          </div>
        </div>
      </div>
    </Link>
  );
}
