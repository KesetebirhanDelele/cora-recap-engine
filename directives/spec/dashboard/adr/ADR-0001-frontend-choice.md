# ADR-0001 — Frontend framework choice

**Status**: Accepted
**Date**: 2026-04-05
**Deciders**: Engineering, Ops

---

## Context

The new production dashboard requires a frontend application that can:
- Render real-time data (live activity feed, health tiles)
- Handle charts and tabular data
- Make authenticated API calls
- Support server-side rendering for fast initial page load on data-heavy views (pipeline trace, exceptions)
- Run in local development without a cloud environment

The existing Streamlit dashboard (`execution/dashboard.py`) is a Python-only tool that is retained in parallel. The new dashboard is a net-new system with different operational characteristics: it must support operator actions, real-time updates, and a structured multi-page layout.

---

## Decision

**Next.js (React) with Recharts** is selected as the frontend framework and chart library.

---

## Rationale

### Why Next.js over plain React
- Server-side rendering (`getServerSideProps`) allows the pipeline trace and exception views to load with Postgres data on the first response, avoiding a loading flash.
- App Router (Next.js 14+) provides layout-level auth wrappers that keep operator action pages consistently gated.
- Next.js has a large ecosystem, well-documented deployment patterns (Docker, static export), and no paid tier.
- The team already uses Python/FastAPI; Next.js keeps the frontend in a well-understood React model without requiring a full custom webpack setup.

### Why Recharts over alternatives
- Pure React, no Canvas-required fallback. Works with SSR.
- No paid tier. MIT license.
- Sufficient for line charts (trends), bar charts (intent distribution, tier breakdown), and simple KPI tiles.
- Alternatives considered:

| Library | Rejected reason |
|---|---|
| Chart.js | Not React-native; requires wrapper; Canvas-based (SSR issues) |
| D3 | Too low-level for this use case; requires custom animation and layout code |
| Highcharts | Paid license for commercial use |
| Victory | Less actively maintained than Recharts |
| Tremor | Paid tier for advanced components; dependency on their hosted design system |

### Why not Vue, Svelte, or Angular
- No existing team expertise documented.
- Next.js/React is the default industry choice for internal ops dashboards.
- Switching cost if engineering expands would be lower with React.

### Why not a low-code solution (Retool, Metabase, etc.)
- Paid tier at production scale.
- Cannot implement WebSocket-based real-time updates.
- Cannot implement custom operator action flows with audit logging.
- Would create a vendor dependency that contradicts the "no paid tools" constraint.

---

## Consequences

### Accepted
- A Node.js runtime is required in the deployment environment for the frontend build and server.
- Next.js SSR adds latency to initial page loads compared to a pure SPA; this is acceptable given the data-heavy nature of trace and exception views.
- Frontend type safety requires maintaining TypeScript types in `dashboard-ui/types/index.ts` that mirror the API Pydantic schemas. These must be kept in sync manually until a code generator is added.

### Deferred
- A type-safe API client generated from the FastAPI OpenAPI schema (e.g., via `openapi-typescript`) is desirable but deferred to Phase 7.
- Authentication via JWT (vs. shared Bearer token) is deferred to Phase 7 per OQ-03.
