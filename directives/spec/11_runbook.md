# spec/11_runbook.md

## Setup
1. Provision Postgres.
2. Provision Redis.
3. Configure API and worker services.
4. Configure GHL, Synthflow, OpenAI, and Google Sheets credentials.
5. Apply schema/migrations.
6. Seed prompt registry and campaign tier policies.

## Local run
1. Start Postgres and Redis.
2. Load `.env` values.
3. Start API service.
4. Start worker service.
5. Start sheet mirror sync job if shadow mode is enabled.
6. Post test webhook events.

## Testing
- unit tests for routing and gating
- integration tests for GHL, Synthflow, Redis/RQ, and Sheets sync
- replay tests for dedupe
- shadow tests for prompts

## Troubleshooting
- duplicate task -> inspect dedupe key and task_events
- missing summary -> inspect consent result and transcript quality
- lost callback -> inspect scheduled_jobs and Redis queue state
- GHL write failure -> inspect API key and location config
- sheet drift -> inspect shadow_sheet_rows and reconciliation job output

## Reporting runbook notes
- Validate KPI values against authoritative SQL queries after schema or logic changes.
- Validate filtering and cross-filtering behavior after reporting-model changes.
- Do not treat missing tooltip or drill-down behavior as a defect in the current phase.