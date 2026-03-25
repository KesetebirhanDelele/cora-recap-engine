# E2E Test Report — Cora Recap Engine

**Generated:** 2026-03-23
**Test suite:** `tests/e2e/test_flows.py`
**Run command:** `python -m pytest tests/e2e/test_flows.py -v`
**Result:** 31 passed, 0 failed
**Execution time:** ~1.1 seconds

---

## Summary

| Scenario | Description | Tests | Result |
|----------|-------------|-------|--------|
| SC1 | New Lead — first voicemail (None → 0) | 4 | PASS |
| SC2 | New Lead — second voicemail (0 → 1) | 2 | PASS |
| SC3 | New Lead — third voicemail (1 → 2) | 2 | PASS |
| SC4 | New Lead — terminal voicemail (2 → 3) | 3 | PASS |
| SC5 | Cold Lead — first voicemail (None → 0) | 2 | PASS |
| SC6 | Not Interested — lead closed | 3 | PASS |
| SC7 | Cold Lead Reactivation (re_engaged) | 2 | **GAP CONFIRMED** |
| SC8 | Enrollment — campaign terminated | 5 | PASS |
| SC9 | Callback with time — outbound scheduled | 3 | PASS |
| SC10 | Campaign switch (New Lead → Cold Lead) | 5 | PASS |

---

## SC1 — New Lead: First Voicemail (tier: None → 0)

**Input:** New Lead, `ai_campaign_value=None`, Synthflow webhook `call_status=voicemail`
**Expected:** tier advances to `'0'`, `launch_outbound_call` job scheduled at ~120 min

| Test | Actual | Result |
|------|--------|--------|
| `ai_campaign_value == '0'` after voicemail | ✓ tier='0' | PASS |
| `launch_outbound_call` job exists (status=pending) | ✓ job found | PASS |
| `CallEvent` row created with correct `call_id` | ✓ row found | PASS |
| `run_at` ≈ now + 120 min (new_vm_tier_none setting) | ✓ within ±1 min window | PASS |

**Notes:** Full flow tested: `process_call_event` → `CallEvent` created → `process_voicemail_tier` → tier advanced.

---

## SC2 — New Lead: Second Voicemail (tier: 0 → 1)

**Input:** New Lead, `ai_campaign_value='0'`, voicemail webhook
**Expected:** tier=`'1'`, retry job scheduled

| Test | Actual | Result |
|------|--------|--------|
| `ai_campaign_value == '1'` | ✓ tier='1' | PASS |
| `launch_outbound_call` pending | ✓ job found | PASS |

---

## SC3 — New Lead: Third Voicemail (tier: 1 → 2)

**Input:** New Lead, `ai_campaign_value='1'`, voicemail webhook
**Expected:** tier=`'2'`, retry scheduled (not yet terminal when finalize=False)

| Test | Actual | Result |
|------|--------|--------|
| `ai_campaign_value == '2'` | ✓ tier='2' | PASS |
| retry job scheduled when `new_vm_tier_2_finalize=False` | ✓ job found | PASS |

---

## SC4 — New Lead: Terminal Voicemail (tier: 2 → 3)

**Input:** New Lead, `ai_campaign_value='2'`, `new_vm_tier_2_finalize=True`
**Expected:** tier=`'3'`, no retry job, GHL write `AI Campaign=No` executed

| Test | Actual | Result |
|------|--------|--------|
| `ai_campaign_value == '3'` | ✓ tier='3' | PASS |
| no `launch_outbound_call` pending | ✓ no job | PASS |
| GHL `update_contact_fields` called with `{'AI Campaign': 'No'}` | ✓ call captured | PASS |

**Notes:** GHL write is shadow-gated in tests. The mock was called with the correct payload, confirming the finalization path executes correctly.

---

## SC5 — Cold Lead: First Voicemail (tier: None → 0)

**Input:** Cold Lead, `ai_campaign_value=None`, voicemail webhook
**Expected:** tier=`'0'`, retry job uses `cold_vm_tier_none` delay (120 min, not new lead 5 min)

| Test | Actual | Result |
|------|--------|--------|
| `ai_campaign_value == '0'` | ✓ tier='0' | PASS |
| retry `run_at` ≥ now + 119 min (cold delay) | ✓ confirmed ~120 min | PASS |

**Notes:** Test specifically sets `new_vm_tier_none=5` vs `cold_vm_tier_none=120` to prove the correct delay map is selected by campaign.

---

## SC6 — Not Interested: Lead Closed

**Input:** Lead leaves transcript `"I'm not interested"` / `"no thanks not interested"`
**Expected:** `status='closed'`, no outbound call, `do_not_call` remains False

| Test | Actual | Result |
|------|--------|--------|
| `status == 'closed'` | ✓ closed | PASS |
| no pending `launch_outbound_call` | ✓ no job | PASS |
| `do_not_call == False` (not_interested ≠ do_not_call) | ✓ False | PASS |

**Notes:** `not_interested` and `do_not_call` are distinct intents. Not-interested closes the lead; do-not-call additionally sets the suppression flag. This distinction is validated here.

---

## SC7 — Cold Lead Reactivation (re_engaged) — KNOWN GAP

**Input:** Cold Lead leaves transcript `"actually I'm interested now"`
**Expected (when fixed):** campaign switches to `New Lead`
**Actual:** `process_voicemail_tier` fails with `KeyError: 're_engaged'`

### Gap Description

`detect_intent("actually I'm interested now")` correctly returns `intent='re_engaged'`.

`handle_intent()` dispatches via `_HANDLERS[intent]` (in `app/core/intent_actions.py`).

`_HANDLERS` does **not** contain an entry for `'re_engaged'`.

This raises `KeyError: 're_engaged'` inside `handle_intent()`, which propagates up through `process_voicemail_tier`, causing:
- Job status set to `'failed'`
- `ExceptionRecord` created (type=`voicemail_tier_failed`, severity=`critical`)
- Campaign switch to `New Lead` **never executes**
- Tier advancement **never executes**

### Evidence

| Test | Actual | Result |
|------|--------|--------|
| `process_voicemail_tier` raises `KeyError` | ✓ KeyError raised | PASS (gap confirmed) |
| job status = `'failed'` after run | ✓ status=failed | PASS (gap confirmed) |
| `campaign_name` remains `'Cold Lead'` | ✓ unchanged | PASS (gap confirmed) |

### Fix Required

Add `_handle_re_engaged` to `app/core/intent_actions.py` and register it in `_HANDLERS`:

```python
def _handle_re_engaged(session, contact_id, phone, entities, settings) -> None:
    """
    Cold lead expressed renewed interest — no status change, no scheduling.

    The campaign switch (Cold Lead → New Lead) is handled by the campaign
    switching hook in process_voicemail_tier immediately after handle_intent().
    This handler is a no-op; it exists only so _HANDLERS dispatch doesn't fail.
    """
    logger.info(
        "handle_intent: re_engaged — campaign switch will follow | contact_id=%s",
        contact_id,
    )
```

Register it in `_HANDLERS`:
```python
"re_engaged": _handle_re_engaged,
```

When this fix is applied:
1. SC7 tests asserting `KeyError` and `status=failed` will FAIL (they test the broken state)
2. Those tests must be replaced with positive assertions: `campaign_name='New Lead'`, `status` unchanged
3. The two SC7 tests should be kept in the file until the fix is merged, clearly marked `EXPECTED FAIL`

---

## SC8 — Enrollment: Campaign Terminated

**Input:** Lead leaves transcript `"I want to enroll"`
**Expected:** `status='enrolled'`, `ai_campaign_value='3'`, GHL write, no retry job

| Test | Actual | Result |
|------|--------|--------|
| `status == 'enrolled'` | ✓ enrolled | PASS |
| `ai_campaign_value == '3'` | ✓ tier='3' | PASS |
| no `launch_outbound_call` pending | ✓ no job | PASS |
| GHL `update_contact_fields({'AI Campaign': 'No'})` called | ✓ call captured | PASS |
| `evaluate_campaign_switch("New Lead", "enrolled") is None` | ✓ None | PASS |

**Notes:** Enrollment is distinct from terminal voicemail (`status='enrolled'` vs `status='active'` at tier 3). Both set `ai_campaign_value='3'` and write GHL, but enrollment preserves the lead as a conversion.

---

## SC9 — Callback with Time: Outbound Call Scheduled

**Input:** Lead says `"call me back tomorrow"` in transcript
**Expected:** `launch_outbound_call` scheduled with `run_at > now + 12h`

| Test | Actual | Result |
|------|--------|--------|
| `launch_outbound_call` job exists | ✓ job found | PASS |
| `run_at > now + 12h` (far future, not fallback 2h) | ✓ ~24h out | PASS |
| `ai_campaign_value` unchanged (None) | ✓ tier=None | PASS |

**Notes:** `"call me back tomorrow"` resolves to `now + 24h` via `_extract_callback_datetime`. The test asserts `> 12h` to distinguish the extracted datetime from the 2h fallback, without hardcoding the exact 24h.

---

## SC10 — Campaign Switch: New Lead + interested_not_now → Cold Lead

**Input:** New Lead says `"not right now maybe later"` in transcript
**Expected:** `campaign_name='Cold Lead'`, `status='nurture'`, `next_action_at` set, no retry call

| Test | Actual | Result |
|------|--------|--------|
| `campaign_name == 'Cold Lead'` | ✓ switched | PASS |
| `status == 'nurture'` | ✓ nurture | PASS |
| `next_action_at is not None` | ✓ set (now + 7 days) | PASS |
| no `launch_outbound_call` pending | ✓ no job | PASS |
| Cold Lead + `uncertain` → stays Cold Lead | ✓ no switch | PASS |

**Notes:** Both actions execute: `_handle_interested_not_now` sets `status='nurture'` and `next_action_at`, then `evaluate_campaign_switch` returns `'Cold Lead'` and `apply_campaign_switch` updates the row.

---

## Environment

| Item | Value |
|------|-------|
| DB | SQLite in-memory (module-scoped engine, function-scoped session) |
| Redis | None (queues patched to return `None`) |
| GHL writes | Shadow-gated (mocked with `MagicMock`) |
| Postgres | Not used (SQLite) |
| New Lead VM delays | 120 / 2880 / 2880 min (production-equivalent) |
| Cold Lead VM delays | 120 / 2880 / 2880 min (production-equivalent) |
| nurture_delay_days | 7 days |

---

## Known Gaps

| Gap ID | Location | Description | Impact |
|--------|----------|-------------|--------|
| GAP-01 | `app/core/intent_actions.py` | `_HANDLERS` missing `'re_engaged'` entry | SC7 fails with KeyError; Cold Lead reactivation (campaign switch Cold→New) never executes in production |

**GAP-01 Resolution:** Add `_handle_re_engaged` no-op handler to `_HANDLERS`. The campaign switch itself is already implemented in `process_voicemail_tier` via `evaluate_campaign_switch`/`apply_campaign_switch`; it just never reaches that code because `handle_intent` raises first.

---

## Files Produced

| File | Purpose |
|------|---------|
| `tests/e2e/__init__.py` | Package marker |
| `tests/e2e/helpers.py` | DB query helpers (lead, job, call event, exception assertions) |
| `tests/e2e/seed.py` | Seed data generator (leads, call events, jobs) |
| `tests/e2e/test_flows.py` | 31 scenario tests across 10 scenarios |
| `execution/simulate_webhook.py` | CLI webhook simulator for manual/ngrok testing |
| `docs/audit_queries.sql` | SQL queries for operational inspection and debugging |
| `reports/e2e_test_report.md` | This report |
