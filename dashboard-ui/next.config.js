/** @type {import('next').NextConfig} */
const nextConfig = {
  // Dashboard API origin — must match DASHBOARD_API_URL env var
  env: {
    NEXT_PUBLIC_API_URL: process.env.DASHBOARD_API_URL || "http://localhost:8001",
    NEXT_PUBLIC_WS_URL: process.env.WS_URL || "ws://localhost:8001",
  },
};

module.exports = nextConfig;
