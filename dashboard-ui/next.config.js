/** @type {import('next').NextConfig} */
const apiOrigin = process.env.DASHBOARD_API_URL || "http://localhost:8001";

const nextConfig = {
  // Dashboard API origin — used server-side and as rewrite destination
  env: {
    NEXT_PUBLIC_API_URL: apiOrigin,
    NEXT_PUBLIC_WS_URL: process.env.WS_URL || "ws://localhost:8001",
  },
  // Proxy /dashboard/* and /health through the Next.js server so browser
  // calls are same-origin (port 3000). Eliminates CORS entirely and means
  // NEXT_PUBLIC_API_URL only needs to be reachable from the Next.js container,
  // not from the user's browser.
  async rewrites() {
    return [
      {
        source: "/dashboard/:path*",
        destination: `${apiOrigin}/dashboard/:path*`,
      },
      {
        source: "/health",
        destination: `${apiOrigin}/health`,
      },
    ];
  },
};

module.exports = nextConfig;
