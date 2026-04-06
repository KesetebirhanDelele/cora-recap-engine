"use client";

import Link from "next/link";
import type { ReactNode } from "react";

interface Props {
  title: string;
  subtitle?: string;
  topbarRight?: ReactNode;
  children: ReactNode;
  fullWidth?: boolean;
}

export default function PageShell({ title, subtitle, topbarRight, children, fullWidth }: Props) {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#f1f5f9",
        fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        color: "#1e293b",
        WebkitFontSmoothing: "antialiased",
      } as React.CSSProperties}
    >
      {/* Topbar */}
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          height: 48,
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          display: "flex",
          alignItems: "center",
          padding: "0 1.5rem",
          gap: "0.875rem",
          boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
        }}
      >
        <Link
          href="/"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.35rem",
            color: "#94a3b8",
            textDecoration: "none",
            fontSize: "0.875rem",
            fontWeight: 500,
            transition: "color 0.15s",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = "#64748b"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = "#94a3b8"; }}
        >
          ← Dashboard
        </Link>

        <span style={{ width: 1, height: 16, background: "#e2e8f0", flexShrink: 0 }} />

        <span style={{ fontSize: "0.95rem", fontWeight: 700, color: "#1e293b", letterSpacing: "-0.01em" }}>
          {title}
        </span>

        {topbarRight && (
          <div style={{ marginLeft: "auto" }}>
            {topbarRight}
          </div>
        )}
      </div>

      {/* Content */}
      <div
        style={{
          maxWidth: fullWidth ? "none" : 1000,
          margin: "0 auto",
          padding: "1.5rem 1.5rem 3rem",
        }}
      >
        {/* Page heading */}
        <div style={{ marginBottom: "1.25rem" }}>
          <h1
            style={{
              margin: 0,
              fontSize: "1.35rem",
              fontWeight: 800,
              color: "#0f172a",
              letterSpacing: "-0.025em",
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <p style={{ margin: "0.2rem 0 0", fontSize: "0.9rem", color: "#475569" }}>
              {subtitle}
            </p>
          )}
        </div>

        {children}
      </div>
    </div>
  );
}
