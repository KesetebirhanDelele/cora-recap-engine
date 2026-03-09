# spec/06_architecture.md

## Overview
Hybrid Python architecture with API layer + background workers.

## Components
1. API service: webhook intake, dashboard APIs, replay APIs.
2. Worker service: AI jobs, CRM writes, retries, delays, callback jobs.
3. SQL Server: authoritative store for state, jobs, audit, exceptions, and mirrored Google Sheet data.
4. Redis + RQ: queue and job execution.
5. GHL adapter: contacts, notes, fields, tasks.
6. Synthflow adapter: delayed callback creation.
7. OpenAI service: transcript analysis, summary generation, consent detection, voicemail content.
8. Google Sheets mirror: active during shadow mode; data mirrored into SQL Server for comparison and cutover tracking.

## Key trade-offs
- SQL Server chosen by business requirement despite earlier Postgres drafts.
- Redis/RQ chosen for simple delayed jobs while keeping canonical state in SQL Server.
- GHL remains CRM authority while SQL Server is campaign/process authority.
- Summary generation remains in scope because final source docs require it.
- One shared tier engine with per-campaign policies reduces complexity while preserving campaign flexibility.

## Risks and mitigations
- Duplicate replays -> dedupe keys.
- Lost delayed jobs -> canonical scheduled_jobs in SQL Server.
- GHL auth/key drift -> critical alerting.
- Prompt regressions -> versioned prompt registry and shadow evaluation.
- Shadow/data drift between Sheets and DB -> reconciliation jobs and dashboards.

