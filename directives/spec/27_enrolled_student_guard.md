# Spec 27 — Enrolled-Student Guard

| Area | Status |
|---|---|
| Student classification helper (`app/core/student_guard.py`) | **DONE.** `check_is_student(phone, settings)` — reuses spec/23's `call_classification` classifier, adds a "not enrolled" negation carve-out. Fails open. Unit-tested. |
| `enter_campaign()` gate | **DONE.** Skips campaign entry; logs an `info` line with the reason; **no** exception (see below); no jobs scheduled/cancelled. |
| `launch_outbound_call_job()` gate | **DONE.** Belt-and-suspenders — cancels the job; logs a `warning` line with the reason; **no** exception. Catches jobs scheduled before this guard shipped, or entered via a path other than `enter_campaign()` (nurture scheduler, voicemail-tier retries). |
| Suppression visibility | **Log line only, no dashboard exception/alert.** Until the GHL-side fix lands, GHL re-enrolls every current student into this campaign on each workflow re-evaluation, so a suppression is *expected list churn* — nothing an operator can action. Identical treatment to the `do_not_call` guard (PROGRESS.md 2026-09-09). The server log (`reason=` + `matched_tags=`) is the audit trail for pushing the GHL fix. |
| GHL-side root cause (stale `AI Campaign` field on converted students; workflow-trigger exclusion) | **NOT IN THIS REPO.** Tracked with Ali / the GHL team — see Out-of-scope. |

## Self-contained problem statement

### Business goal
Stop Cora's outbound Cold Lead / New Lead campaigns from cold-pitching people
who are already enrolled Colaberry students. Observed incident (2026-09-09,
first day after the GHL→Cora call-launch cutover went live): of 176 contacts
GHL enrolled into an outbound campaign that day, 7 classified as students —
at least one confirmed enrolled student (`+12067427323`, "enrolled student"
tag, `customer` type) was auto-dialed twice, and an in-person-bootcamp
student (`+13132885957`, "ipbc student") actually connected and received the
cold sales pitch. A second in-person student (`+19198026833`) was dialed
three times.

### Root cause (for context — the fix below is the Cora-side backstop only)
GHL's Cold Lead and New Lead workflows trigger on a stale `AI Campaign =
Yes` / `AI Campaign Name` custom field that is **never cleared when a lead
converts to an enrolled student**. GHL therefore keeps re-enrolling current
students into the cold-pitch campaign. Neither `enter_campaign()` nor
`launch_outbound_call_job()` checked the contact's student status before
dialing — the only reason more students weren't called is the spec/22
urgent-escalation guard happening to block a few of them.

The durable fix is GHL-side (clear the field on enrollment; exclude
student-tagged / won-opportunity contacts from the workflow trigger). That
is out of scope for this repo and tracked with Ali. This spec is the
Cora-side guarantee: **"current students must not be cold-called" is
enforced at the door, regardless of what GHL's list contains.**

### Relevant systems and files
- `app/core/student_guard.py` — new module, `check_is_student(phone, settings)`.
- `app/core/call_classification.py` — existing (spec/23). Its
  `extract_classification_signals()` + `classify_from_known_signals()` are
  reused unchanged; a `"support"` result means "student".
- `app/core/campaigns.py::enter_campaign()` — checked immediately after the
  existing urgent-escalation guard, before cancelling jobs / scheduling.
- `app/worker/jobs/outbound_jobs.py::launch_outbound_call_job()` — checked
  immediately after the existing urgent-escalation guard.
- `app/adapters/ghl.py` — `GHLClient.search_contact_by_phone()` +
  `get_contact()`, both existing sync reads.

### Input data
The contact's live GHL record: `tags` (primary signal — per Kes 2026-08-25
and spec/23, the only reliably-populated one), the three "who you are"
picklist fields, and `enrollment_date`. Cora's `LeadState` has no student
concept, so this must read GHL live at campaign-entry / dial time. The
contact is resolved by phone (`contact_id` in this data model is usually the
phone number itself, not a GHL id, so a `search_contact_by_phone()` step is
required before `get_contact()` — the same reason `_has_spam_likely_tag()`
is a no-op for phone-shaped `contact_id`s).

### Expected outputs
`check_is_student()` returns a dict (`ghl_contact_id`,
`classification_source`, `matched_tags`) when the contact classifies as a
student, else `None`. When non-`None`, callers hold/cancel the outbound call
and emit a single structured log line carrying the reason
(`classification_source`) and `matched_tags`. **No `ExceptionRecord` is
created** — see "Suppression visibility" above.

### Known edge cases
- **`"registered - not enrolled"` tag** — spec/23's keyword rule matches
  `"enrolled"` as a substring, so this lead (registered for an info session
  but never enrolled — a *sales* target) is a false positive. The guard
  carves it out: if every student-matched tag also matches a negation marker
  (`"not enrolled"`, `"unenrolled"`, `"non-enrolled"`, `"not a student"`,
  `"prospective"`, `"potential student"`), the contact is **not** treated as
  a student.
- **`"dropped out student"` tag** — a former student and arguably a legit
  win-back target, but it is **blocked** (conservative default): a generic
  AI cold-pitch is not the right re-engagement path, and every suppression
  is surfaced as an exception for a human to triage into a deliberate
  win-back flow. Revisit only with product input.
- **GHL lookup fails / times out** — fail **open** (log a warning, allow the
  campaign). A GHL outage must not halt all outbound calling; this is a
  backstop, not the primary control, and the primary control is the
  GHL-side fix.
- **Contact not found in GHL** — allow (brand-new lead Cora has a row for
  but GHL search can't resolve; nothing to classify).
- **`settings is None`** in `enter_campaign()` — skip the student check
  (can't construct a `GHLClient`), same graceful-degradation pattern the
  pause-flag check already uses.

### Out of scope
- The GHL-side root-cause fixes: clearing `AI Campaign` / `AI Campaign Name`
  when a contact becomes an enrolled student, and adding a
  student-tag / won-opportunity exclusion filter to the Cold Lead and New
  Lead workflow triggers. Belongs to the GHL team (Ali) — this repo cannot
  edit GHL workflows.
- A deliberate "win-back" campaign for dropped-out / former students.
- Caching the student classification on `LeadState` to avoid the per-entry
  GHL read — current outbound volume (~30 calls/day) makes 2 extra GET
  requests per campaign entry a non-issue; revisit under spec/18 scaling if
  volume grows.

## Acceptance criteria
1. Given a GHL contact with a tag containing `"student"` or `"enrolled"`
   (e.g. `"enrolled student"`, `"ipbc student"`, `"data analytics
   student"`), when `enter_campaign()` is called for that contact, then no
   jobs are scheduled, a single `info` log line records the reason, and no
   `ExceptionRecord` is created.
2. Same signal, but the job was already scheduled before this guard shipped
   — when `launch_outbound_call_job()` runs, then the job is cancelled (not
   dialed), a `warning` log line records the reason, and no `ExceptionRecord`
   is created.
3. Given the contact's only student-matched tag is `"registered - not
   enrolled"` (or another negation-marked tag), when either gate runs, then
   it passes through — this is a sales target, not a student.
4. Given the GHL lookup raises / times out, when either gate runs, then it
   passes through (fails open) and logs a warning — no exception, campaign
   proceeds.
5. Given `search_contact_by_phone()` returns no match, when either gate
   runs, then it passes through with no suppression.
6. Given a `"Current Student"` value on any of the three picklist fields (no
   student tag), when either gate runs, then it suppresses (source
   `ghl_select_option_*` / `ghl_who_you_are`).

## Constraint architecture
- **Must** check both `enter_campaign()` and `launch_outbound_call_job()` —
  neither alone covers every entry path (nurture scheduler and
  voicemail-tier retries call `launch_outbound_call_job()` directly).
- **Must** reuse `call_classification.py`'s classifier rather than
  re-implementing tag rules — spec/23 owns that contract and it is tested.
  The only addition here is the negation carve-out, which lives in
  `student_guard.py`, not in `call_classification.py`.
- **Must** fail **open** on any GHL read error — a backstop that can cause a
  full outbound outage is worse than the gap it closes.
- **Must not** require a schema change.
- **Must not** raise a dashboard exception or alert for a student
  suppression — it is expected GHL list churn, not an operator-actionable
  event (matches the `do_not_call` guard). The suppression must still leave
  a trace: a single structured log line with `reason=` and `matched_tags=`.
- **Must not** silently *dial* a suppressed contact — the log line and the
  job cancellation together are the audit record.
- **Preference:** one GHL contact fetch per gate; accept the belt-and-
  suspenders double-read across the two gates rather than threading state.
- **Escalation trigger:** if a grep of the suppression log lines shows a
  contact being blocked that is *not* actually a student (or a real student
  slipping through), that is a signal the tag vocabulary needs a controlled
  list — escalate to product, do not keep widening keyword heuristics.

## Eval design
`tests/unit/test_student_guard.py` (student tag → dict; `"enrolled
student"`; `"ipbc student"`; `"registered - not enrolled"` → None;
picklist `"Current Student"` → dict; no contact → None; GHL raises → None
+ warning; empty phone → None) + `tests/unit/test_campaigns.py`
(skip-on-student, no-exception-on-student, proceed-on-non-student,
skip-check-when-settings-None) + `tests/unit/test_outbound_jobs.py`
(cancel-on-student without exception, proceed-on-None).
