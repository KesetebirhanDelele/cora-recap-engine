# spec/03_constraints.md

## Must
- Use Postgres as authoritative state.
- Use Redis + RQ for queue execution.
- Use GHL per-location API keys.
- Keep summary generation, consent detection, and consent-gated writeback in scope.
- Create a task for every completed non-voicemail call.
- Leave task due date blank.
- Let GHL own final task assignment.
- Use canonical voicemail state model `None -> 0 -> 1 -> 2 -> 3` across campaigns.
- Cold Lead timing remains 2 hours, 2 days, 2 days, then final stop.
- Keep Google Sheets active during shadowing and mirror sheet data into Postgres.
- Store canonical scheduled-job records in Postgres.
- Support admin dashboard retry and recovery controls.

## Must-not
- Do not send SMS/email directly from this app.
- Do not use Google Sheets as authoritative state.
- Do not create duplicate call tasks or duplicate summary writebacks.
- Do not write summary to GHL when consent is `NO`.

## Preferences
- One codebase with separate API and worker processes.
- FastAPI-style API surface.
- Postgres for durable state plus active Sheets mirror in shadow mode.

## Escalation triggers
- Missing `call_id` and unresolved identity.
- Postgres read/write failure on required state.
- Invalid tier transition.
- GHL API authentication failure.
- Persistent transient dependency failure beyond retry budget.

## Definition of Done
- [ ] Inbound, outbound cold, and outbound new flows documented.
- [ ] Postgres replaces Postgres in all future-state docs.
- [ ] Summary + consent behavior documented.
- [ ] .env variable template included.
- [ ] Dashboard and recovery actions documented.
- [ ] Unified tier model documented.
- [ ] Sheets shadow mirroring documented.

