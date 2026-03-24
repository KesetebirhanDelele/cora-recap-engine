# Non-Unit Test Scenarios

All scenarios covered by integration, eval, and end-to-end tests.
Generated from: `tests/integration/`, `tests/evals/`, `tests/unit/test_e2e_scenarios.py`

---

## How to Run

```bash
# End-to-end and eval regression (no external dependencies)
python -m pytest tests/unit/test_e2e_scenarios.py tests/evals/test_regression_suite.py -v

# All evals including fixture-based (skipped until fixtures loaded)
EVAL_FIXTURES=1 pytest tests/evals/ -v

# Integration tests (requires real Postgres + INTEGRATION_TESTS=1)
INTEGRATION_TESTS=1 pytest tests/integration/ -v
```

---

## 1. Integration Tests — `tests/integration/test_migrations.py`

Requires: real Postgres instance, `INTEGRATION_TESTS=1`

### INT-01 — Migration 0001 applies and rolls back cleanly

| | |
|---|---|
| **Input** | Empty Postgres database, Alembic config pointing to it |
| **Action** | `alembic upgrade 0001` → `alembic downgrade base` |
| **Expected output** | All 8 tables created on upgrade; all tables removed on downgrade; no errors |

---

### INT-02 — Full migration to head applies cleanly

| | |
|---|---|
| **Input** | Empty Postgres database |
| **Action** | `alembic upgrade head` (applies all versions: 0001–0006) |
| **Expected output** | All migrations apply without error; schema is at head |

---

### INT-03 — Partial unique index enforced (one `created` task per call)

| | |
|---|---|
| **Input** | A `call_event` row + one `task_event` with `status='created'` for that call |
| **Action** | Insert a second `task_event` with `status='created'` for the same `call_event_id` |
| **Expected output** | `IntegrityError` raised — index `uq_task_events_one_success` prevents duplicate |
| **Negative case** | Insert second row with `status='failed'` for same call → **no error** (partial index only constrains `created`) |

---

### INT-04 — JSONB columns store and retrieve Python dicts

| | |
|---|---|
| **Input** | `scheduled_jobs` row with `payload_json = {"tier": 0, "delay_minutes": 120, "campaign": "Cold Lead"}` |
| **Action** | Insert row, query it back |
| **Expected output** | Row retrieved successfully; JSONB column stores and returns dict without error |

---

## 2. Eval Regression Tests — `tests/evals/test_regression_suite.py`

Always run (no flags needed). These lock in invariants that must survive every code change.

---

### REG-01 — Consent YES allows writeback

| | |
|---|---|
| **Input** | `ConsentOutput(consent="YES")` |
| **Expected output** | `allows_writeback = True` |

---

### REG-02 — Consent NO blocks writeback

| | |
|---|---|
| **Input** | `ConsentOutput(consent="NO")` |
| **Expected output** | `allows_writeback = False` |

---

### REG-03 — Consent UNKNOWN blocks writeback

| | |
|---|---|
| **Input** | `ConsentOutput(consent="UNKNOWN")` |
| **Expected output** | `allows_writeback = False` |

---

### REG-04 — Blank transcript produces blank summary without API call

| | |
|---|---|
| **Input** | `transcript = ""`, mocked OpenAI client |
| **Expected output** | `student_summary = ""`; `chat_completion` never called |

---

### REG-05 — Blank transcript produces UNKNOWN consent without API call

| | |
|---|---|
| **Input** | `transcript = None`, mocked OpenAI client |
| **Expected output** | `consent = "UNKNOWN"`; `chat_completion` never called |

---

### REG-06 — Cold Lead None→0 delay is 120 minutes (2 hours)

| | |
|---|---|
| **Input** | `get_cold_lead_policy(None)` with `cold_vm_tier_none_delay_minutes=120` |
| **Expected output** | `policy.delay_minutes = 120` |

---

### REG-07 — Cold Lead 0→1 delay is 2880 minutes (2 days)

| | |
|---|---|
| **Input** | `get_cold_lead_policy("0")` with `cold_vm_tier_0_delay_minutes=2880` |
| **Expected output** | `policy.delay_minutes = 2880` |

---

### REG-08 — Cold Lead 1→2 delay is 2880 minutes (2 days)

| | |
|---|---|
| **Input** | `get_cold_lead_policy("1")` with `cold_vm_tier_1_delay_minutes=2880` |
| **Expected output** | `policy.delay_minutes = 2880` |

---

### REG-09 — Cold Lead 2→3 is terminal, no Synthflow callback

| | |
|---|---|
| **Input** | `get_cold_lead_policy("2")` |
| **Expected output** | `is_terminal = True`; `schedule_synthflow_callback = False` |

---

### REG-10 — Canonical tier sequence is unified across all campaigns

| | |
|---|---|
| **Input** | Each tier value: `None, "0", "1", "2", "3"` passed to `_get_next_tier()` |
| **Expected output** | `None→"0"`, `"0"→"1"`, `"1"→"2"`, `"2"→"3"`, `"3"→None` |

---

### REG-11 — Terminal tier is always 3

| | |
|---|---|
| **Input** | `Settings()` (defaults) |
| **Expected output** | `vm_final_stop_value = 3` |

---

### REG-12 — Task creation enabled by default for completed calls

| | |
|---|---|
| **Input** | `Settings()` (defaults) |
| **Expected output** | `task_create_on_completed_call = True` |

---

### REG-13 — GHL task due date is blank

| | |
|---|---|
| **Input** | `GHLClient.build_task_payload("Test task")` |
| **Expected output** | `payload["dueDate"] = None` |

---

### REG-14 — GHL task has no manual assignment

| | |
|---|---|
| **Input** | `GHLClient.build_task_payload("Test task")` |
| **Expected output** | `"assignedTo"` not present in payload — GHL owns assignment |

---

### REG-15 — Shadow mode disables GHL writes

| | |
|---|---|
| **Input** | `Settings(ghl_write_mode="shadow", ghl_write_shadow_log_only=True)` |
| **Expected output** | `ghl_writes_enabled = False` |

---

### REG-16 — Shadow write returns structured response, not None

| | |
|---|---|
| **Input** | `GHLClient.create_task()` in shadow mode |
| **Expected output** | Returns `{"shadow": True, ...}`; no HTTP request made |

---

### REG-17 — Model prefix `openai/` is stripped

| | |
|---|---|
| **Input** | `OpenAIClient._strip_prefix("openai/gpt-4o-mini")` |
| **Expected output** | `"gpt-4o-mini"` |

---

### REG-18 — All required prompt families are registered

| | |
|---|---|
| **Input** | `list_families()` after importing `app.prompts` |
| **Expected output** | All four registered: `lead_stage_classifier`, `student_summary_generator`, `summary_consent_detector`, `vm_content_generator` |

---

### REG-19 — All prompt families have a v1 version

| | |
|---|---|
| **Input** | `is_registered(family, "v1")` for each of the four families |
| **Expected output** | All return `True` |

---

### REG-20 — Summary writeback requires consent by default

| | |
|---|---|
| **Input** | `Settings()` (defaults) |
| **Expected output** | `summary_writeback_requires_consent = True` |

---

### REG-21 — Idempotency TTL is 90 days

| | |
|---|---|
| **Input** | `Settings()` (defaults) |
| **Expected output** | `idempotency_ttl_days = 90` |

---

### REG-22 — Reporting views exist in migration 0002

| | |
|---|---|
| **Input** | File contents of `migrations/versions/0002_reporting_views.py` |
| **Expected output** | Both `fact_call_activity` and `fact_kpi_daily` present in migration file |

---

## 3. Eval Fixture Tests — `tests/evals/test_shadow_eval_harness.py`

These tests run only with `EVAL_FIXTURES=1`. Six are currently skipped pending real transcript fixtures.

### EVAL-01 — Completed call, consent YES → non-blank student summary *(skipped)*

| | |
|---|---|
| **Input** | Real transcript fixture with explicit consent YES |
| **Expected output** | `student_summary` is non-empty |
| **Status** | Skipped — fixture not loaded |

---

### EVAL-02 — Completed call, consent NO → summary generated, writeback blocked *(skipped)*

| | |
|---|---|
| **Input** | Real transcript fixture with explicit consent NO |
| **Expected output** | `student_summary` non-empty; GHL write not called |
| **Status** | Skipped — fixture not loaded |

---

### EVAL-03 — Blank transcript → blank summary, no API call *(skipped)*

| | |
|---|---|
| **Input** | `transcript = None` |
| **Expected output** | `student_summary = ""`; `summary_offered = False` |
| **Status** | Skipped — fixture not loaded |

---

### EVAL-04 — Transcript with consent YES → `ConsentOutput.consent == "YES"` *(skipped)*

| | |
|---|---|
| **Input** | Real transcript fixture with explicit YES consent phrase |
| **Expected output** | `consent = "YES"` |
| **Status** | Skipped — fixture not loaded |

---

### EVAL-05 — Transcript with consent NO → `ConsentOutput.consent == "NO"` *(skipped)*

| | |
|---|---|
| **Input** | Real transcript fixture with explicit NO consent phrase |
| **Expected output** | `consent = "NO"` |
| **Status** | Skipped — fixture not loaded |

---

### EVAL-06 — Ambiguous transcript → `ConsentOutput.consent == "UNKNOWN"` *(skipped)*

| | |
|---|---|
| **Input** | Real transcript fixture with no clear consent signal |
| **Expected output** | `consent = "UNKNOWN"` |
| **Status** | Skipped — fixture not loaded |

---

### EVAL-07 — Consent NO blocks `allows_writeback` (always run)

| | |
|---|---|
| **Input** | `ConsentOutput(consent="NO")` |
| **Expected output** | `allows_writeback = False` |

---

### EVAL-08 — Consent UNKNOWN blocks `allows_writeback` (always run)

| | |
|---|---|
| **Input** | `ConsentOutput(consent="UNKNOWN")` |
| **Expected output** | `allows_writeback = False` |

---

### EVAL-09 — Blank transcript produces blank summary, no API call (always run)

| | |
|---|---|
| **Input** | `transcript = ""`, mocked OpenAI client |
| **Expected output** | `student_summary = ""`; `chat_completion` not called |

---

### EVAL-10 — Model prefix stripped in `model_used` field (always run)

| | |
|---|---|
| **Input** | Settings with `openai_model_student_summary="openai/gpt-4o-mini"`, non-blank transcript |
| **Expected output** | `result.model_used` does not start with `"openai/"` |

---

### EVAL-11 — Prompt family and version stamped on every summary output (always run)

| | |
|---|---|
| **Input** | Non-blank transcript, mocked client returning a summary |
| **Expected output** | `result.prompt_family` and `result.prompt_version` are both non-empty |

---

## 4. End-to-End Scenario Tests — `tests/unit/test_e2e_scenarios.py`

SQLite in-memory DB. All external adapters (GHL, OpenAI, Synthflow) mocked.

---

### E2E-01 — Completed call, consent YES → `allows_writeback = True`

| | |
|---|---|
| **Input** | Transcript: `"Yes please send me a summary by email."`, mocked AI returning `consent="YES"` |
| **Expected output** | `consent_result.consent = "YES"`; `allows_writeback = True` |

---

### E2E-02 — Consent YES → summary row and task event both created exactly once

| | |
|---|---|
| **Input** | `CallEvent` with non-blank transcript + `SummaryOutput(consent="YES")` + `create_crm_task` job |
| **Action** | `_persist_summary()` then `create_crm_task()` in shadow mode |
| **Expected output** | `summary_results` row with `summary_consent="YES"` and `student_summary="Great call!"`; `task_events` row with `status="created"`; GHL `create_task` called exactly once |

---

### E2E-03 — Consent NO → `allows_writeback = False`

| | |
|---|---|
| **Input** | Transcript: `"No thanks, I don't want the summary."`, mocked AI returning `consent="NO"` |
| **Expected output** | `consent_result.consent = "NO"`; `allows_writeback = False` |

---

### E2E-04 — Consent NO → summary persisted for audit, GHL write never called

| | |
|---|---|
| **Input** | `ConsentOutput(consent="NO")` |
| **Expected output** | `allows_writeback = False`; `GHLClient.update_contact_fields` not called |

---

### E2E-05 — Blank transcript → no OpenAI call, blank summary

| | |
|---|---|
| **Input** | `transcript = None`, mocked OpenAI client |
| **Expected output** | `student_summary = ""`; `summary_offered = False`; `chat_completion` not called |

---

### E2E-06 — Blank transcript → UNKNOWN consent, no writeback

| | |
|---|---|
| **Input** | `transcript = ""`, mocked OpenAI client |
| **Expected output** | `consent = "UNKNOWN"`; `allows_writeback = False`; `chat_completion` not called |

---

### E2E-07 — Duplicate `dedupe_key` rejected at DB level

| | |
|---|---|
| **Input** | Two `CallEvent` rows with identical `dedupe_key` |
| **Expected output** | `IntegrityError` raised on second insert — DB unique constraint is the primary idempotency guard |

---

### E2E-08 — Duplicate CRM task creation blocked by application layer

| | |
|---|---|
| **Input** | Same `call_event_id` passed to `create_crm_task` twice |
| **Expected output** | GHL `create_task` called exactly once; second job detects existing `task_events` row with `status="created"` and skips |

---

### E2E-09 — Cold Lead None→0: 120-minute delay, Synthflow required

| | |
|---|---|
| **Input** | `get_cold_lead_policy(None)` with `cold_vm_tier_none_delay_minutes=120` |
| **Expected output** | `next_tier="0"`; `delay_minutes=120`; `schedule_synthflow_callback=True`; `is_terminal=False` |

---

### E2E-10 — Cold Lead 0→1: 2880-minute delay, Synthflow required

| | |
|---|---|
| **Input** | `get_cold_lead_policy("0")` with `cold_vm_tier_0_delay_minutes=2880` |
| **Expected output** | `next_tier="1"`; `delay_minutes=2880`; `schedule_synthflow_callback=True` |

---

### E2E-11 — Cold Lead 1→2: 2880-minute delay, Synthflow required

| | |
|---|---|
| **Input** | `get_cold_lead_policy("1")` with `cold_vm_tier_1_delay_minutes=2880` |
| **Expected output** | `next_tier="2"`; `delay_minutes=2880`; `schedule_synthflow_callback=True` |

---

### E2E-12 — Cold Lead 2→3: terminal, no Synthflow, zero delay

| | |
|---|---|
| **Input** | `get_cold_lead_policy("2")` |
| **Expected output** | `next_tier="3"`; `is_terminal=True`; `schedule_synthflow_callback=False`; `delay_minutes=0` |

---

### E2E-13 — Cold Lead full sequence delays: 2h, 2d, 2d

| | |
|---|---|
| **Input** | All three Cold Lead tier policies evaluated together |
| **Expected output** | Delays are exactly `[120, 2880, 2880]` (2h, 2d, 2d) |

---

### E2E-14 — Duplicate callback skipped when pending job exists

| | |
|---|---|
| **Input** | A `scheduled_jobs` row with `job_type="synthflow_callback"`, `status="pending"` for a contact |
| **Action** | `has_pending_callback(session, contact_id)` |
| **Expected output** | Returns `True` — caller must skip scheduling a second callback |

---

### E2E-15 — New callback allowed when prior one is completed

| | |
|---|---|
| **Input** | A `scheduled_jobs` row with `job_type="synthflow_callback"`, `status="completed"` for a contact |
| **Action** | `has_pending_callback(session, contact_id)` |
| **Expected output** | Returns `False` — completed job does not block new scheduling |

---

### E2E-16 — Missing GHL API key raises `ConfigError` immediately

| | |
|---|---|
| **Input** | `GHLClient` with no `ghl_api_key` in settings |
| **Action** | `client.search_contact_by_phone("+15551234567")` |
| **Expected output** | `ConfigError` raised matching `"GHL_API_KEY"` — no silent failure, no API call |

---

### E2E-17 — Missing Synthflow API key raises `ConfigError` immediately

| | |
|---|---|
| **Input** | `SynthflowClient` with no `synthflow_api_key` in settings |
| **Action** | `client.schedule_callback(phone="+15550000000")` |
| **Expected output** | `ConfigError` raised — no silent failure |

---

### E2E-18 — Canonical tier sequence: None→0→1→2→3

| | |
|---|---|
| **Input** | Each tier value `[None, "0", "1", "2"]` passed to `_get_next_tier()` |
| **Expected output** | Returns `["0", "1", "2", "3"]` respectively |

---

### E2E-19 — Tier 3 is always terminal

| | |
|---|---|
| **Input** | `_get_next_tier("3")` |
| **Expected output** | Returns `None` — no further advancement possible |

---

### E2E-20 — Both Cold Lead and New Lead reach terminal tier 3

| | |
|---|---|
| **Input** | `get_cold_lead_policy("2")` and `get_new_lead_policy("2")` with all delays configured |
| **Expected output** | Both return `next_tier="3"` and `is_terminal=True` |

---

### E2E-21 — UNKNOWN consent treated as NO (no writeback)

| | |
|---|---|
| **Input** | `ConsentOutput(consent="UNKNOWN")` |
| **Expected output** | `allows_writeback = False` — ambiguous consent never triggers GHL write |

---

### E2E-22 — Only YES allows writeback (exhaustive check)

| | |
|---|---|
| **Input** | Three `ConsentOutput` objects: `consent="YES"`, `"NO"`, `"UNKNOWN"` |
| **Expected output** | `YES → True`; `NO → False`; `UNKNOWN → False` |

---

## Summary

| Suite | File | Count | Requires |
|---|---|---|---|
| Integration | `tests/integration/test_migrations.py` | 4 | Postgres + `INTEGRATION_TESTS=1` |
| Eval regression | `tests/evals/test_regression_suite.py` | 22 | Nothing (always run) |
| Eval fixture-based | `tests/evals/test_shadow_eval_harness.py` | 6 active + 6 skipped | `EVAL_FIXTURES=1` for skipped |
| End-to-end scenarios | `tests/unit/test_e2e_scenarios.py` | 22 | Nothing (SQLite in-memory) |
| **Total** | | **54** | |
