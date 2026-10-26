# 40 - Sign-up intake trend (Refactored sign-ups in GHL)

**Status:** built and tested 2026-10-26 on `feat/signup-intake-dashboard`; deploy pending Kes's go-ahead.

## Problem
Ads send people to the Refactored sign-up page. Since 2026-10-23 each sign-up is written to GHL as a contact (source `api v1`) by the Refactored
platform. They are not Cora leads (no `lead_state`, no calls), so none of Cora's pages can answer "how many signed up today, and how many of them
have an email or a phone number". Surge on 2026-10-24: 143 in one day, of which 113 arrived in a single hour (the backfill of the first ad run).

## What is counted
| Segment | Rule | Why separate |
|---|---|---|
| `explorer` | tag `src-free_signup`, or source `api v1` without an Open House tag (covers the 8 contacts created on 10-23 before tagging went live) | the ad sign-ups: email only, no phone yet |
| `open_house` | tag `src-open_house` or `brand-open_house` | the only ones with phone numbers; Cora's New Lead flow calls and texts them |

Per segment and Central (America/Chicago) day: `signups`, `with_email`, `with_phone`, `with_both`, `with_campaign` (has a `camp-*` tag),
`utm_fb`, `utm_ig`. Email / phone = the GHL contact field is non-empty.

## Design
- **Hourly worker job** `signup_intake_sync` (`app/worker/jobs/signup_intake_jobs.py`, default queue, self-rescheduling like
  `ai_cold_lead_tagging_scan`, claim lease 15 min, started from `worker/main.py`). The work is `services/signup_intake.run_sync`.
- **Tables** (migration 0030): `signup_daily` (PK day, segment; rebuilt for the last 7 days every run, older days untouched; the first run, with an
  empty table, fills the history since 2026-10-21) and `signup_snapshot` (PK taken_at hour, segment; cumulative totals at that hour, one row per
  hour, re-runs in the same hour replace it). `signup_daily` is a cohort view (a phone added next week raises the sign-up day's count);
  `signup_snapshot` records WHEN emails and phones arrived.
- **Safety:** the GHL read (`dateAdded` range filter, paged 100, `searchAfter`) completes before any write; a failed read, or a window too large to page
  (> 30,000 contacts), raises and leaves both tables as they were. Re-runs are idempotent (delete-and-insert of the window inside one transaction;
  the caller commits). Read-only on GHL.
- **API:** `GET /dashboard/signup-intake?days=30` (1-180) -> zero-filled daily series from the first tracked day, all-time totals with email / phone /
  campaign percentages per segment, the cumulative series (last snapshot of each Central day), `last_refreshed`.
- **Page:** Analytics -> "Sign-up Intake" (`dashboard-ui/app/signup-intake`, `SignupIntakeClient.tsx`): two all-time tiles, stacked bars of
  sign-ups per day, lines of with-email / with-phone per day by segment, running totals, and a by-day table. Range buttons 7 / 14 / 30 / 90 days.

## Acceptance
- A dry run against the real GHL on 2026-10-26 (read-only) gave 232 Explorer (232 email, 0 phone, 116 with a campaign tag) and 3 Open House
  (3 email, 3 phone): the same as the manual analysis.
- Days with no sign-ups show as zero; the first day shown is the first tracked day; an empty database says nothing is tracked yet.
- A contact created at 11:30 PM Central counts on that Central day, not the next UTC day.

## Not done / watch
- The live Explorer sign-ups since the backfill carry the campaign tag only 4 times out of 98 (the backfilled batch: 110 of 113). The "campaign tag"
  column makes that visible; the fix is on the Refactored side (Ali).
- A consent column is not shown: no consent field exists in GHL yet. Add it once the sign-up form stores consent.
- A sign-up counted today could later be deleted or merged in GHL; the last-7-days rebuild corrects recent days, older days keep their count.

## Tests
`tests/unit/test_signup_intake.py` (22): classification matrix, Central day boundary, email / phone / both / campaign counting, first-run history
fetch, 7-day rebuild keeping older days, no double counting and a later phone picked up, failed read leaves tables untouched, paging, oversize
window refused, zero-filled trend, cumulative last-snapshot-per-day, empty database, job and migration registration.
