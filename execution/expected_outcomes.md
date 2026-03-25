# Expected Outcomes — Production Simulation Scenarios

All delays are read from `.env` via `app.config.Settings`.
All campaigns are restricted to `"New Lead"` and `"Cold Lead"`.

---

## SC1 — Voicemail Retry Ladder

**Contact:** `sim-sc1-001` | **Campaign:** New Lead | **Start tier:** NULL

| Step | Webhook | Expected DB state after worker completes |
|------|---------|------------------------------------------|
| 1 | voicemail (no transcript) | `ai_campaign_value='0'`, pending `launch_outbound_call` at `now + NEW_VM_TIER_NONE_DELAY_MINUTES` |
| 2 | voicemail (no transcript) | `ai_campaign_value='1'`, pending `launch_outbound_call` at `now + NEW_VM_TIER_0_DELAY_MINUTES` |
| 3 | voicemail (no transcript) | `ai_campaign_value='2'`, pending `launch_outbound_call` at `now + NEW_VM_TIER_1_DELAY_MINUTES` |
| 4 | voicemail (no transcript) | `ai_campaign_value='3'`, **no** `launch_outbound_call` (terminal) |

Between each step: `launch_outbound_call` job is intercepted by the runner (marked completed) to prevent real Synthflow calls. The next webhook represents the "retry call went to voicemail again."

**Required env vars:** `NEW_VM_TIER_NONE_DELAY_MINUTES`, `NEW_VM_TIER_0_DELAY_MINUTES`, `NEW_VM_TIER_1_DELAY_MINUTES`, `NEW_VM_TIER_2_FINALIZE=true`

---

## SC2 — Interested Not Now

**Contact:** `sim-sc2-001` | **Campaign:** New Lead | **Start tier:** NULL

**Transcript:** `"I'm interested but not right now"`

**Detected intent:** `interested_not_now`

| Check | Expected |
|-------|----------|
| `lead_state.status` | `'nurture'` |
| `lead_state.campaign_name` | `'Cold Lead'` (campaign switch: New Lead + interested_not_now → Cold Lead) |
| `lead_state.next_action_at` | `NOW() + NURTURE_DELAY_DAYS days` |
| Pending `launch_outbound_call` | None |
| Pending `process_voicemail_tier` | None |

**Required env vars:** `NURTURE_DELAY_DAYS` (default 7)

---

## SC3 — Uncertain

**Contact:** `sim-sc3-001` | **Campaign:** New Lead | **Start tier:** NULL

**Transcript:** `"I'm not sure… let me think about it"`

**Detected intent:** `uncertain`

| Check | Expected |
|-------|----------|
| `lead_state.status` | `'nurture'` |
| `lead_state.campaign_name` | `'Cold Lead'` (campaign switch: New Lead + uncertain → Cold Lead) |
| `lead_state.next_action_at` | `NOW() + max(1, NURTURE_DELAY_DAYS // 2) days` (shorter window than interested_not_now) |
| Pending `launch_outbound_call` | None |

**Note:** `uncertain` uses a **shorter** nurture window than `interested_not_now` (half of `NURTURE_DELAY_DAYS`, minimum 1 day) — the system checks back sooner for on-the-fence leads.

**Required env vars:** `NURTURE_DELAY_DAYS` (default 7)

---

## SC4 — Callback Request

**Contact:** `sim-sc4-001` | **Campaign:** New Lead | **Start tier:** NULL

**Transcript:** `"call me back in 20 minutes"`

**Detected intent:** `callback_with_time` (datetime extracted: `now + 20 min`)

| Check | Expected |
|-------|----------|
| Pending `launch_outbound_call` | 1 job |
| `run_at` | `≈ now + 20 minutes` (extracted from transcript) |
| `lead_state.status` | `'active'` (unchanged — callback is not a state change) |
| Tier | NULL (unchanged — intent short-circuits tier logic) |

**Note on delays:** For `callback_with_time`, the delay is **extracted from the transcript** (20 min), not from `.env`. The fallback delay when no time can be extracted is `CALLBACK_FALLBACK_MINUTES = 120` — this constant is hardcoded in `app/core/intent_actions.py` and is not currently settable via `.env`.

> **MINOR GAP:** `CALLBACK_FALLBACK_MINUTES` is not in settings. If configurable fallback is needed, add `callback_fallback_minutes: int = 120` to `Settings` and read it in `intent_actions.py`.

---

## SC5 — Not Interested

**Contact:** `sim-sc5-001` | **Campaign:** New Lead | **Start tier:** NULL

**Transcript:** `"no thanks, not interested"`

**Detected intent:** `not_interested`

| Check | Expected |
|-------|----------|
| `lead_state.status` | `'closed'` |
| `lead_state.do_not_call` | `False` (not_interested ≠ do_not_call; do_not_call requires explicit opt-out language) |
| Pending `launch_outbound_call` | None |
| Pending jobs (any type) | None (all other pending jobs cancelled) |

---

## SC6 — Enrollment (Campaign Exit)

**Contact:** `sim-sc6-001` | **Campaign:** New Lead | **Start tier:** NULL

**Transcript:** `"I want to enroll"`

**Detected intent:** `enrolled`

| Check | Expected |
|-------|----------|
| `lead_state.status` | `'enrolled'` |
| `lead_state.ai_campaign_value` | `'3'` (terminal — no further tier advancement) |
| Pending `launch_outbound_call` | None |
| Pending jobs (any type) | None |
| GHL write | `AI Campaign = 'No'` (shadow-gated; captured in logs when GHL_WRITE_MODE=shadow) |

**Note:** `enrolled` is **not** a campaign switch — it is termination. `evaluate_campaign_switch("New Lead", "enrolled")` returns `None`.

---

## SC7 — Duplicate Call Protection

**Contact:** `sim-sc7-001` | **Campaign:** New Lead | **Start tier:** NULL

**Test:** Same `call_id` POSTed twice to `/v1/webhooks/calls`.

| Check | Expected | Actual |
|-------|----------|--------|
| `call_events` rows for `call_id` | 1 (dedupe via `dedupe_key`) | ✓ 1 row |
| `process_call_event` jobs created | 2 (one per webhook POST) | 2 jobs |
| `process_voicemail_tier` jobs created | 2 (one per process_call_event) | 2 jobs |
| Final `ai_campaign_value` | Expected: `'0'` | **Actual: `'1'`** |

> **KNOWN GAP — Duplicate Tier Advancement:**
> The `dedupe_key` on `call_events` prevents duplicate *CallEvent rows* but does NOT prevent duplicate *tier advancement*. Both `process_voicemail_tier` jobs process the same contact and advance the tier sequentially (None→0, then 0→1).
>
> Mitigation (without changing app logic): Ensure upstream systems (Synthflow, ngrok) do not send duplicate `call_id` webhooks. Synthflow retry logic should use a different `call_id` for retried calls.

---

## SC8 — Multi-Step Journey

**Contact:** `sim-sc8-001` | **Campaign:** New Lead | **Start tier:** NULL

| Step | Webhook | Intent | Expected state after |
|------|---------|--------|----------------------|
| 1 | voicemail (no transcript) | — | tier=`'0'`, outbound scheduled |
| 2 | voicemail (no transcript) | — | tier=`'1'`, outbound scheduled |
| 3 | voicemail + "I'm not sure let me think" | `uncertain` | status=`nurture`, campaign=`Cold Lead` |
| 4 | voicemail + "call me back in 20 minutes" | `callback_with_time` | outbound scheduled ~20 min |
| 5 | voicemail + "I want to enroll" | `enrolled` | status=`enrolled`, tier=`'3'` |

Between steps 1→2 and 2→3: runner intercepts `launch_outbound_call` job.
Step 3 (uncertain): no outbound call scheduled — runner sends next webhook directly.
Step 4 (callback): outbound scheduled but with future run_at — runner intercepts before worker executes.
Step 5 (enrollment): terminal — no further jobs.

**Required env vars:** New Lead VM tier delays, `NURTURE_DELAY_DAYS`

---

## SC9 — SMS + Email Scheduling

**Contact:** `sim-sc9-001` | **Campaign:** New Lead | **Start tier:** NULL

| After step | Expected |
|------------|----------|
| First voicemail (tier None→0) | `send_sms` job pending, `run_at ≈ now + SMS_FOLLOWUP_DELAY_MINUTES` |
| Second voicemail (tier 0→1) | `send_email` job pending (attempt=2), `run_at ≈ now + EMAIL_FOLLOWUP_DELAY_DAYS * 24h` |

**Runner validates:** Job existence and `run_at` window only. Does NOT execute `send_sms_job` or `send_email_job` (those require OpenAI for content generation).

**Required env vars:** `SMS_FOLLOWUP_DELAY_MINUTES` (default 30), `EMAIL_FOLLOWUP_DELAY_DAYS` (default 1)

> **Note:** `send_sms_job` and `send_email_job` call `generate_sms()` / `generate_email()` which require `OPENAI_API_KEY`. If not set, these jobs will **fail** when the worker eventually executes them. This does not affect SC9 validation (scheduling only) or SC10 (reply check runs before AI generation).

---

## SC10 — Reply Stops Messaging

**Contact:** `sim-sc10-001` | **Campaign:** New Lead | **Start tier:** NULL

| Step | Action | Expected |
|------|--------|----------|
| 1 | Send voicemail | `send_sms` job scheduled at `now + SMS_FOLLOWUP_DELAY_MINUTES` |
| 2 | Simulate reply (insert `InboundMessage`) | `has_recent_reply()` will return `True` |
| 3 | Fast-forward `send_sms` job to `run_at = NOW()` | Worker picks up job immediately |
| 4 | Wait for `send_sms` job to complete | Status = `'completed'` |
| 5 | Check `outbound_messages` | **0 rows** for this contact (suppressed) |

**Key assertion:** `send_sms_job` completes cleanly (status=`completed`) but creates NO `OutboundMessage` row — the reply check runs before AI generation and short-circuits.

**Required env vars:** `SMS_FOLLOWUP_DELAY_MINUTES` (default 30)

---

## Env Var Requirements Summary

| Variable | Default | Required for |
|----------|---------|-------------|
| `NEW_VM_TIER_NONE_DELAY_MINUTES` | **None** | SC1, SC8 (BLOCKER if missing) |
| `NEW_VM_TIER_0_DELAY_MINUTES` | **None** | SC1, SC8 (BLOCKER if missing) |
| `NEW_VM_TIER_1_DELAY_MINUTES` | **None** | SC1, SC8 (BLOCKER if missing) |
| `NEW_VM_TIER_2_FINALIZE` | **None** | SC1, SC8 (BLOCKER if missing) |
| `COLD_VM_TIER_NONE_DELAY_MINUTES` | 120 | SC5 |
| `NURTURE_DELAY_DAYS` | 7 | SC2, SC3, SC8 |
| `SMS_FOLLOWUP_DELAY_MINUTES` | 30 | SC9, SC10 |
| `EMAIL_FOLLOWUP_DELAY_DAYS` | 1 | SC9 |

> `CALLBACK_FALLBACK_MINUTES` is hardcoded at 120 in `app/core/intent_actions.py`. Not in settings.
