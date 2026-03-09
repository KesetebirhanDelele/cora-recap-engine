# directives/spec/13_autonomous_execution_contract.md

## Purpose
This document defines the mandatory execution contract for Claude acting as an autonomous worker on the Cora Recap Engine project.

It closes the remaining gaps required for unattended autonomous implementation by specifying:
- exact stop conditions
- approval-gated boundaries
- concurrency and transaction rules
- phased build order
- required outputs per phase
- required tests per phase
- repo hygiene rules
- unresolved external IDs
- do-not-continue conditions

This document is binding for autonomous execution.

---

## 1. Autonomous Execution Goal
Claude may build this application in autonomous mode only if it follows this contract in addition to:
- `CLAUDE.md`
- all files under `directives/spec/`
- all ADRs under `directives/adr/`

Autonomous execution means Claude may proceed through multiple implementation phases without waiting for human feedback **only when** none of the stop conditions in this document are triggered.

---

## 2. Exact Stop Conditions
Claude must stop and ask before continuing if any of the following occur:

### External-system ambiguity
1. A required external ID is missing and cannot be safely inferred.
2. A GHL custom field label is known but its actual implementation identifier is required and unavailable.
3. A required pipeline ID, owner ID, location ID, or custom field ID is missing for a write path.
4. Synthflow requires an identifier or request shape not already locked by the spec.
5. A Google Sheets spreadsheet or tab name needed for shadow sync is unknown.

### Schema ambiguity
6. SQL schema design requires choosing between materially different data models not already resolved by the specs or ADRs.
7. A migration would destroy, rename, or repurpose data in a way that cannot be proven safe.
8. A uniqueness rule conflicts with a source-system behavior or real historical data shape.

### Authentication / authorization uncertainty
9. Credential shape or auth flow for GHL, Synthflow, Google Sheets, Redis, Postgres, or OpenAI is unclear.
10. A change would alter access-control, secret handling, session behavior, or webhook verification semantics beyond what is specified.

### Source conflict
11. Two source docs or specs conflict on business logic and the conflict is not explicitly resolved by a newer binding decision.
12. A source document implies runtime behavior that contradicts current requirements, ADRs, or constraints.

### Production-safety uncertainty
13. A code path could cause unintended writes to GHL or other external systems while the system is in read-only or shadow mode.
14. Retry behavior could create duplicate tasks, duplicate summaries, duplicate campaign-state transitions, or duplicate callbacks.
15. Worker concurrency could produce double-processing without a clear locking/deduping strategy.

### Operational unknowns
16. A required environment variable is missing and no safe default exists.
17. A required local or test dependency cannot be initialized safely.
18. The next phase depends on outputs from a previous phase that are missing or failing.

If any stop condition is hit, Claude must halt that line of implementation, record the blocker, and ask only the minimum clarifying question required.

---

## 3. Do-Not-Continue-If-Missing List
Claude must not continue past the relevant phase if any of the following are still missing:

### Before writing external integrations
- GHL API key auth shape confirmed
- GHL location ID available for the target environment
- confirmed custom field labels or IDs for required write fields
- Synthflow API key and model ID fields present in config contract

### Before enabling write-capable code paths
- write mode controls exist and default safely
- idempotency rules for external side effects are implemented
- tests exist for duplicate replay protection

### Before implementing shadow sync
- spreadsheet IDs and tab names are defined in config
- SQL shadow tables are defined
- reconciliation semantics are defined

### Before implementing worker retries
- retry caps are specified
- lease/claim behavior is implemented
- terminal exception behavior is defined

### Before finishing the dashboard
- exception model exists
- operator actions are authorization-scoped
- operator conflict rules are implemented

---

## 4. Approval-Gated Boundaries
Claude must request approval before crossing any of these boundaries:

1. introducing or modifying database schema migrations
2. changing authentication or secret-handling behavior
3. changing webhook verification logic
4. enabling real external writes where the previous default was read-only or shadow
5. changing retry/idempotency semantics
6. deleting files or replacing an established module structure
7. changing canonical tier behavior
8. changing summary consent semantics
9. changing build tooling, deployment assumptions, or runtime architecture

Claude may scaffold code, tests, configs, and docs autonomously **up to** these boundaries, but must pause before committing a boundary-crossing decision not already explicitly approved by the specs.

---

## 5. Concurrency and Transaction Rules
These rules are mandatory for implementation.

### 5.1 Transaction boundaries
Claude must ensure the following operations are transaction-safe:
- lead/campaign state transitions
- scheduled-job claims
- exception status transitions
- operator actions that mutate workflow state
- dedupe lock acquisition before external side effects

### 5.2 Locking rules
Claude must implement one of the following for mutable workflow state:
- row-level locking, or
- optimistic concurrency with version checks

Minimum requirement:
- only one worker may claim a scheduled job at a time
- only one mutation path may advance a lead’s campaign value for a given effective event
- only one successful task creation record may exist for a given completed call event

### 5.3 Required unique constraints
Claude must encode or simulate uniqueness for:
- one call-event record per dedupe key
- one successful GHL task per completed call event
- one successful summary writeback per completed call event
- one effective campaign-state transition per `(call_id, action_type, campaign_value_before)`
- one scheduled job execution claim per scheduled job instance

### 5.4 Retry claim / lease behavior
Workers must not blindly process queued work.

Required behavior:
- a worker claims a scheduled job using an atomic state change
- claimed work records `claimed_by`, `claimed_at`, and lease expiration where relevant
- abandoned or expired claims may be re-queued safely
- retries must preserve original dedupe keys
- terminal failures must open or update an exception record

### 5.5 Operator conflict handling
If two operators invoke actions simultaneously:
- the first successful state transition wins
- the second action must fail cleanly with a conflict or no-op result
- all operator actions must be audit logged
- force-finalize, cancel-future-jobs, and retry-now must check current state before mutating it

### 5.6 External side effects
All external side effects must be protected by idempotency logic.

This includes:
- GHL task creation
- GHL notes or custom-field updates when mutation is enabled
- summary writeback
- Synthflow callback scheduling

---

## 6. Repo Hygiene Rules
Claude must explicitly update the following repository files when changes make them stale:

### README.md
Update `README.md` whenever changes affect:
- setup steps
- architecture overview
- local run instructions
- required services
- testing workflow
- environment variables
- shadow-mode or write-mode behavior

### .gitignore
Update `.gitignore` whenever changes introduce:
- generated files
- temp files
- test artifacts
- runtime logs
- cache directories
- local database dumps
- `.env` variants
- editor/tooling artifacts

### Migration/setup docs
Update setup docs, runbook, or migration docs whenever changes affect:
- schema initialization
- migration order
- seed data requirements
- local dependency startup
- operational cutover steps

### Mandatory hygiene rule
A code change that requires documentation or ignore-rule updates is **not done** until those files are updated.

---

## 7. Phased Build Order Contract
Claude must build in the following phase order unless explicitly told otherwise.

### Phase 1 — Scaffold repo
**Goal**: establish repository structure and base app layout.

Required outputs:
- repo folders created
- app/package skeleton
- API/worker module skeletons
- config module skeleton
- tests skeleton
- README scaffold
- `.gitignore` scaffold

Required tests/checks:
- import smoke test
- basic lint/type/test command runs
- app boots without integration credentials

### Phase 2 — Config and environment contract
**Goal**: encode config loading, mode flags, and `.env.example` handling.

Required outputs:
- settings loader
- environment variable schema
- safe defaults for read-only/shadow behavior
- secrets never hard-coded

Required tests/checks:
- config validation tests
- missing-required-env tests
- mode-flag interpretation tests

### Phase 3 — SQL schema and migrations
**Goal**: implement authoritative data model and migration chain.

Required outputs:
- schema definitions
- migrations
- unique constraints
- indexes
- seed data or seed scripts where required

Required tests/checks:
- migration up/down tests where possible
- uniqueness tests
- transaction tests for core state updates
- restart-safe scheduled-job persistence tests

### Phase 4 — GHL adapter (read-first)
**Goal**: implement read-safe GHL integration first.

Required outputs:
- GHL client
- auth handling
- read operations
- write-mode gates
- payload builders for future writes

Required tests/checks:
- auth/config tests
- client request-shape tests
- read-only mode tests
- shadow-mode payload generation tests

### Phase 5 — OpenAI prompt registry and AI layer
**Goal**: implement prompt registry and structured output handling.

Required outputs:
- prompt family registry
- prompt version metadata
- summary generation wrapper
- consent detection wrapper
- voicemail content wrapper

Required tests/checks:
- schema validation tests
- blank transcript behavior tests
- prompt/version stamping tests
- shadow eval harness stubs

### Phase 6 — Worker / queue / retry engine
**Goal**: implement Redis/RQ jobs and retry-safe execution.

Required outputs:
- worker entrypoints
- queue routing
- scheduled-job claim/lease logic
- retry helpers
- exception creation hooks

Required tests/checks:
- claim/lease tests
- duplicate retry tests
- restart recovery tests
- dead-letter/terminal failure tests

### Phase 7 — Synthflow integration
**Goal**: implement callback scheduling.

Required outputs:
- Synthflow client
- callback payload builder
- campaign-policy-driven scheduling path

Required tests/checks:
- request-shape tests
- delay-policy tests
- duplicate callback prevention tests

### Phase 8 — Dashboard + operator actions
**Goal**: implement exception visibility and operator controls.

Required outputs:
- exception list/query APIs
- retry-now action
- retry-delay action
- cancel-future-jobs action
- force-finalize action
- audit logging for operator actions

Required tests/checks:
- authorization tests
- operator conflict tests
- audit trail tests

### Phase 9 — Google Sheets shadow sync
**Goal**: mirror live sheet data into Postgres without making Sheets authoritative.

Required outputs:
- sheets client
- mirror jobs
- shadow tables
- reconciliation status model

Required tests/checks:
- sync tests
- reconciliation tests
- proof that production routing does not depend on Sheets

### Phase 10 — Integration hardening and evals
**Goal**: close the loop on autonomous safety.

Required outputs:
- end-to-end happy-path tests
- duplicate replay tests
- summary consent tests
- tier engine tests
- operational docs updated

Required tests/checks:
- eval suite passes
- README updated
- `.gitignore` updated
- runbook updated

Claude must not skip phases where required outputs are missing.

---

## 8. Required Outputs Per Phase
Claude must explicitly produce and verify the following categories of artifacts as work proceeds:
- source code
- tests
- config/schema changes
- docs updates
- migration files when applicable
- prompt registry updates when applicable
- repo hygiene updates when applicable

If a phase changes architecture or developer workflow, `README.md` and relevant docs must be updated in the same phase.

---

## 9. Required Test Minimums Per Phase
Minimum autonomous standard:
- every phase must include at least one verification artifact
- behavior-changing phases must include automated tests
- external integration phases must include mocks or contract tests
- concurrency-sensitive phases must include replay or conflict tests
- migration phases must include schema integrity checks

Claude must not declare a phase complete based only on inspection.

---

## 10. Unresolved External IDs List
These are known placeholders and must remain configuration-driven until real values are supplied.

### GHL field mappings
- `GHL_FIELD_VM_EMAIL_HTML`
- `GHL_FIELD_VM_EMAIL_SUBJECT`
- `GHL_FIELD_VM_SMS_TEXT`
- `GHL_FIELD_LAST_CALL_STATUS`
- `GHL_FIELD_MARK_AS_LEAD`
- `GHL_FIELD_NOTES`
- `GHL_TASK_PIPELINE_ID`
- `GHL_TASK_DEFAULT_OWNER_ID`

### Integration identifiers
- exact per-environment GHL location IDs
- exact per-environment Synthflow model IDs
- exact Google Sheets IDs and tab names where shadow mode is enabled

Claude may scaffold support for these, but must not hard-code guessed production values.

---

## 11. Read/Write Safety Contract
Until explicitly approved otherwise:
- GHL integration must support read-only and shadow modes
- Google Sheets integration must support mirror-only shadow mode
- external writes must be feature-gated
- default unsafe production mutations must not occur silently

If mode semantics are ambiguous in implementation, Claude must stop and ask.

---

## 12. Completion Standard for Fully Autonomous Build Mode
The project is considered ready for true hands-off autonomous implementation only when:
- this contract is present
- concurrency rules are encoded in specs and implementation
- repo hygiene rules are explicit
- stop conditions are explicit
- phased build order is explicit
- required tests per phase are explicit
- unresolved external IDs are isolated behind config
- approval-gated boundaries are explicit

Until then, the system is only safe for assisted autonomous mode.

