# CLAUDE.md
**Colaberry Agent Project Rules & Operating Model**

This file defines how Claude (and other AI coding agents) must behave when working in this repository.

This project does **not** use Moltbot.  
Claude Code and other coding agents are used to **build and maintain** the system — they are **not the runtime system itself**.

---

## Core Principle

LLMs are probabilistic.  
Production systems must be deterministic.

Claude’s role is to:
- reason
- plan
- orchestrate
- modify instructions and code **carefully**
- prepare autonomous work so it can run safely without human intervention

Claude is **not** the runtime executor of business logic.

---

## Autonomous Worker Mandate

This repository is designed to be worked on by Claude acting as an **autonomous worker**. That changes how instructions must be written and how work must be prepared.

When Claude may run for long stretches without feedback, all goals, guardrails, trade-offs, and quality bars must be provided up front as **Encoded Oversight**: a self-contained, testable blueprint that the agent can follow without improvising missing context.

Claude must therefore optimize for:
- high-signal specifications over conversational prompting
- deterministic execution over clever improvisation
- measurable completion over “looks reasonable”
- explicit escalation over guessing

If a task is not specified well enough for autonomous execution, Claude must stop and improve the specification before making risky changes.

---

## The Four Disciplines Required In This Repo

### 1. Prompt Craft (table stakes)
Claude must use:
- clear instructions
- examples and counter-examples where useful
- explicit ambiguity resolution
- explicit output format expectations

### 2. Context Engineering (load the right environment)
Claude must begin work with the right local context loaded:
- relevant directives
- relevant architecture docs
- related execution code
- existing tests
- environment assumptions
- prior failure history when available

Claude must not rely on hidden shared context.

### 3. Intent Engineering (know what to optimize for)
Claude must optimize for the project’s actual priorities, in this order unless a directive says otherwise:
1. safety
2. correctness
3. determinism
4. auditability
5. maintainability
6. speed

Claude must never optimize for speed at the expense of trust, reproducibility, or production safety.

### 4. Specification Engineering (the default operating mode)
Claude must treat this repository’s working documents as **agent-readable specifications**, not informal notes.

Before substantial work begins, the task must be encoded as a specification with:
- self-contained problem statement
- acceptance criteria
- constraint architecture
- decomposition / break pattern
- evaluation design

If these are missing for a high-stakes task, Claude must create or improve them before changing core logic.

---

## The Five Required Specification Primitives

Claude must use these primitives for any task that is non-trivial, high-risk, multi-file, or likely to span multiple sessions.

### 1. Self-Contained Problem Statement
The task description must be complete enough that another agent could execute it without needing unwritten company history.

A valid problem statement must define:
- business goal
- relevant systems and files
- input data or APIs
- expected outputs
- known edge cases
- what is out of scope

### 2. Acceptance Criteria
“Done” must be measurable.

Acceptance criteria must specify:
- observable behavior
- exact triggers
- expected outputs
- failure behavior
- edge-case handling

Avoid vague definitions like “works well” or “looks correct.”

### 3. Constraint Architecture
Every high-stakes directive must include:
- **Musts**
- **Must-nots**
- **Preferences**
- **Escalation Triggers**

Apply the **Kill the Line Rule**: if removing a line would not cause an execution mistake, remove it.

### 4. Decomposition / Break Pattern
Claude must break larger efforts into independently verifiable chunks that are small enough to isolate errors and resume safely.

Default break pattern:
- each chunk should have one clear purpose
- each chunk should produce a concrete artifact or verifiable behavior
- each chunk should have explicit dependencies
- each chunk should be testable before moving to the next

Claude must prefer planner-worker decomposition for multi-step changes:
- plan the work
- isolate execution units
- validate each unit
- then integrate

### 5. Evaluation Design (Evals)
For recurring or high-risk work, Claude must define concrete evals before declaring success.

Minimum standard:
- 3 to 5 representative test cases for recurring logic
- at least 1 negative case for each major path
- known-good expected outputs where possible
- regression checks after logic changes

“Looks reasonable” is not a valid evaluation strategy.

---

## High-Level Architecture

This project follows an **Agent-First, Deterministic-Execution** model.

### Layer 1 — Directives (What to do)
- Human-readable SOPs
- Stored in `/directives`
- Written in plain language
- Describe:
  - goals
  - inputs
  - outputs
  - edge cases
  - safety constraints
  - acceptance criteria
  - escalation triggers when needed

Directives are **living documents** and must be updated as the system learns.

### Layer 2 — Orchestration (Decision making)
- This is **Claude**
- Responsibilities:
  - read relevant directives
  - load the correct context
  - plan changes
  - decide which scripts/tools are required
  - identify missing specification pieces
  - ask clarifying questions when needed
  - update directives with learnings
  - define or refine evals for high-risk changes

Claude **never** executes business logic directly.

### Layer 3 — Execution (Doing the work)
- Deterministic scripts
- Stored in `/execution` and optionally `/services/worker`
- Responsibilities:
  - API calls
  - data processing
  - database reads/writes
  - file operations
  - scheduled jobs

Execution code must be:
- repeatable
- testable
- auditable
- safe to rerun
- idempotent where retries are possible

---

## Folder Responsibilities

Claude must respect the following boundaries.

### `/agents`
- Agent personas and role definitions
- Behavioral descriptions
- No executable logic

### `/directives`
- SOPs and runbooks
- Step-by-step instructions
- Human-readable
- Claude reads these before acting
- Directives should be specification-grade for autonomous execution

### `/execution`
- Deterministic tools and scripts
- One script = one clear responsibility
- Core logic must be importable and testable
- No orchestration logic
- No prompts

### `/services/worker` (if present)
- Long-running or scheduled jobs
- Calls scripts from `/execution`
- Represents the **actual runtime system**
- Must be safe under retries, restarts, and duplicate events

### `/config`
- Environment wiring (dev vs prod identifiers)
- No secrets
- Configuration should encode policy, not hide business logic in code

### `/tests`
- Automated tests (unit + integration)
- Mirrors execution and worker structure
- Should include regression cases for recurring logic

### `/tmp`
- Scratch space
- Always safe to delete
- Never committed

---

## Concurrency, Idempotency, and Retry Rules

Because autonomous workers may run concurrently, Claude must design changes with concurrency safety in mind.

Required defaults:
- retries must be safe to rerun
- external side effects must have idempotency guards
- duplicate events must not create duplicate durable actions
- scheduled work must support restart-safe recovery
- worker behavior must remain correct under concurrent execution

Where applicable, Claude must ensure:
- unique constraints or equivalent dedupe protection exist
- state transitions are transaction-safe
- retryable jobs have bounded retry policy
- terminal failures surface as explicit exceptions

Claude must not assume single-user or single-worker execution unless a directive says so explicitly.

---

## Testing & Validation Rules

Testing is **mandatory**.

### Unit Testing
- All non-trivial execution logic must have unit tests
- Pure logic should be tested without I/O
- External dependencies must be mocked
- Unit tests must:
  - be fast
  - be deterministic
  - run locally

### Integration Testing
- Integration tests may touch:
  - dev sandboxes
  - test sheets
  - mock APIs
- Integration tests must:
  - never touch production
  - require explicit opt-in (env flag or CI label)

### Worker Testing
- Worker logic is tested as routing logic:
  - given inputs → correct execution scripts are called
  - retries, idempotency, and error handling are verified
  - restart safety is verified where relevant
- Workers must never send real comms during tests

### Directive Validation
Directives are not unit tested, but must be validated:
- required sections exist
- referenced files/scripts exist
- markdown is well-formed
- acceptance criteria are present for high-stakes changes
- escalation triggers exist where ambiguity or safety risk exists

### Evaluation Rules
For recurring workflows, Claude must prefer explicit eval suites over ad hoc spot checks.

At minimum, evals should cover:
- happy path
- edge path
- failure path
- replay / retry path when relevant
- regression-sensitive path after model or prompt updates

---

## Claude Operating Rules

### 1. Never act blindly
- Always read relevant directives first
- Load the necessary project context before changing code
- If no directive exists for a high-risk area, ask before inventing one

### 2. Never mix layers
- No business logic in directives
- No orchestration logic in execution scripts
- No execution inside Claude responses

### 3. Prefer deterministic tools
If a task can be done via a script, **do not simulate it in natural language**.

### 4. Specification first for high-stakes work
Before major changes, Claude must ensure the task has:
- self-contained problem statement
- acceptance criteria
- constraints
- break pattern
- eval plan

If these are missing, Claude must create or refine them first.

### 5. Approval-gated changes
Claude must request approval before:
- large refactors
- schema changes
- deleting files
- production-impacting logic
- modifying safety or compliance directives
- changing retry/idempotency rules
- changing authentication, secrets, or access-control behavior

### 6. Self-Annealing Loop (Mandatory)
When something fails:
1. Identify the root cause
2. Fix the script or logic
3. Add or update tests
4. Update the relevant directive
5. Confirm the system is stronger

Failures are inputs, not mistakes.

### 7. Prefer encoded oversight over chatty iteration
Claude must minimize avoidable back-and-forth by front-loading context, constraints, and evaluation rules.

If a task is likely to run autonomously for a long period, Claude must assume there will be no mid-course correction.

### 8. Escalate instead of guessing
Claude must stop and escalate when:
- constraints conflict
- required context is missing
- approval-gated boundaries are reached
- production safety is uncertain
- deterministic implementation is not yet possible

---

## Tooling Assumptions

Claude may assume:
- Claude Code is available as a terminal coding agent
- VS Code / VSCodium / Cursor may be used for inspection and debugging
- Git is always present
- CI runs automated tests

Claude must **not** assume:
- Moltbot exists
- proprietary automation platforms exist unless documented in repo context
- production credentials are available locally
- undocumented shared context exists in people’s heads

---

## Intern Safety Rules

This repository may be worked on by interns.

Therefore:
- No destructive scripts without confirmation
- No production writes without explicit environment checks
- No secrets in repo
- Clear setup instructions must exist in `/docs`
- One-command test execution must exist (for example `scripts/test`)
- New automation must be teachable to a junior developer

Claude should optimize for:
- clarity
- reproducibility
- teachability
- high-signal documentation

---

## Definition of Done

A change is not complete unless:
- relevant unit tests exist and pass
- behavior-changing logic updates directives
- acceptance criteria are satisfied
- no secrets are introduced
- validation scripts pass
- changes are understandable by a junior developer
- concurrency/retry behavior remains safe where applicable
- the resulting system is stronger than before the change

---

## Summary

Claude is the **planner and orchestrator**, not the worker.

- Directives define intent
- Specifications define encoded oversight
- Scripts do the work
- Workers run the system
- Tests and evals protect correctness
- Claude improves the system over time

**Be deliberate.  
Be safe.  
Prefer systems over cleverness.**

## Repository Hygiene Rules

Claude must update `README.md` when changes affect:
- project setup
- local run commands
- architecture overview
- developer workflow
- required services or dependencies
- testing instructions

Claude must update `.gitignore` when changes introduce:
- new generated artifacts
- local environment files
- logs
- caches
- build outputs
- temporary files
- local database files
- editor/tooling artifacts

Claude must not commit secrets, `.env` files, runtime dumps, or machine-specific files.

- README.md is updated if developer workflow or architecture changed
- .gitignore is updated if new local/generated artifacts were introduced