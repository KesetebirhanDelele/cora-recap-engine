# spec/04_breakdown_plan.md

## Chunk 1: Source parity extraction
Purpose: map the three final source flows into one future-state state machine.
Outputs: parity matrix, route map, field map.
Tests: every current-state path maps to one future-state route.
Dependencies: source docs.
Effort: M

## Chunk 2: API ingest
Purpose: accept webhook events and validate payloads.
Outputs: inbound contracts, auth model, dedupe entry.
Tests: valid event accepted, missing identity rejected.
Dependencies: Chunk 1.
Effort: S

## Chunk 3: Postgres state store
Purpose: persist events, campaign state, exceptions, shadow-mirrored sheet data, and scheduled jobs.
Outputs: schema, indexes, transitions, dedupe model.
Tests: duplicate protection, restart safety, mirror reconciliation.
Dependencies: Chunk 2.
Effort: M

## Chunk 4: GHL adapter
Purpose: contact upsert, field writes, note writes, and task creation.
Outputs: API contract, field mapping layer.
Tests: one task per completed call, finalization writes succeed.
Dependencies: Chunk 3.
Effort: M

## Chunk 5: OpenAI job layer
Purpose: transcript analysis, summary generation, consent detection, voicemail content.
Outputs: prompt registry, structured outputs.
Tests: blank transcript -> blank summary; consent YES/NO gate works.
Dependencies: Chunks 2-4.
Effort: M

## Chunk 6: Redis/RQ workers
Purpose: retries, delays, callbacks, replay, and async processing.
Outputs: queue topology, worker jobs, retry policy.
Tests: delayed jobs survive restart using Postgres canonical state.
Dependencies: Chunks 2-5.
Effort: M

## Chunk 7: Unified tier policy engine
Purpose: apply shared tier numbering with campaign-specific policies.
Outputs: campaign policy configuration, delay maps, action maps, finalization rules.
Tests: New Lead and Cold Lead share state model but can diverge in timing and actions.
Dependencies: Chunks 3-6.
Effort: M

## Chunk 8: Admin dashboard
Purpose: exception queue, state inspection, and operator actions.
Outputs: read APIs, action APIs, audit trail.
Tests: retry/cancel/finalize actions logged.
Dependencies: Chunks 3-7.
Effort: M

## Chunk 9: Active Google Sheets shadow mirror
Purpose: keep Google Sheets live during cutover and mirror sheet data into Postgres.
Outputs: ingestion jobs, reconciliation reports, comparison tooling.
Tests: no production logic depends on Sheets; database remains authoritative.
Dependencies: Chunks 3-8.
Effort: M

