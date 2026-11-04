# Spec 49 — Explorer Call Queue Tagging (accelerator ranking -> GHL tags, drain on connected call)

| Area | Status |
|---|---|
| Spec | **Decisions locked 2026-11-04** (see below). Build starts next session on branch `feat/explorer-call-queue-tagging`. |
| Live feed from the accelerator | **BLOCKED on Ali.** `GET /api/admin/explorer-growth/outreach` needs a signed admin session JWT (4 h expiry); no static token can work. Ali will add a service-token read under `/api/v1/`. Build against a fixture until then. |
| GHL tag-remove | **Does not exist** in `GHLClient`. To be added (step 2). |
| Which GHL sub-accounts the queue's contacts live in, and where Rose's Smart List sits | **OPEN — asked of Ali.** `GHLClient` is bound to one location (`ghl_location_id`). |
| Test contacts | 5 fake contacts in Cora's own location, no phone, no email, tagged `ent-test-delete`. Kes deletes them by hand afterwards. |

---

## Decisions locked (Kes, 2026-11-03/04)

1. **Tags drive the queue** (as Ali specified: stage and score band tags, `ent-priority-call` for the Smart List). **Plus one GHL column (custom field), Score (number)**, created by Kes in Cora's location. GHL Smart Lists sort by field values (confirmed from the GHL contacts screen), and a tag can only carry a band, so the exact score goes in the column and Rose sorts by it. **Stage stays a tag only** (`ent-stage-*`); Rose filters on it in her Smart List. The job writes the Score column on every run, only when the value changed. The field exists in Cora's location (verified read-only 2026-11-04): name `Score`, key `contact.score`, type NUMERICAL, id `UYv8i2BnHkLOJyhc08mR`. (A separate, older `Lifecycle Stage` text field, key `contact.lifecycle_stage`, also exists; it is not used here.) This goes beyond Ali's prompt, so he is told before it ships; if he objects, drop the column and keep the tags (nothing else depends on it). *(Updated 2026-11-04.)*
2. **The job runs nightly.** One run per day.
3. **Stage and Score are null** on every existing contact until the job writes the tags. The score shown to Rose is the band (`ent-score-high/mid/low`), not the number.
4. **No answer keeps the contact on the list.** Only `call_connected = true` after the tag write removes `ent-priority-call`.
5. **Test setup:** 5 fake contacts (`fake_one`..`fake_five`) in Cora's own GHL location, no phone, no email, real `ent-` tags plus `ent-test-delete`. Kes deletes them by hand. Rose is told to ignore them.
6. **Connected-call test:** fake database record first (local database, production call records untouched). Then, only if Kes has checked GHL Automations for anything firing on a new contact or `ent-` tag, a real call from Kes's phone to `fake_five` from the GHL dialer. Confirm the call lands in `staff_call_quality` before relying on it.
7. **Writes to production GHL are approved** for those 5 test contacts only (add/remove tags, create the contacts). Dry run is still the default. No PR without Kes's explicit go-ahead.
8. **Rose's Smart List is a saved filter, not a separate list.** Contacts always stay in GHL; they appear on or drop off Rose's list only because the filter (tag `ent-priority-call`, and/or column values if fields are added later) matches or stops matching. The job therefore never deletes or moves contacts, it only adds and removes tags. Everything else about the list is to be decided later or clarified by Ali.
9. **Demo:** an interactive simulation exists (https://claude.ai/artifact/F8KJUqL64bE6VX2wAS5Bby) and reflects the decisions above.

10. **Second GHL column: `ent_AI_Summary`** (added by Kes on Ali's request, so Rose reads a short journey summary before calling). Verified read-only 2026-11-04: key `contact.ent_ai_summary`, type LARGE_TEXT, id `3yYPrP3zgbG2J5T1jf83`. Not to be confused with the existing `Student Summary` field (`contact.student_summary`), which belongs to the SMS pipeline and is never touched here. **Who writes the summary text is OPEN (asked of Ali).** Default until answered: the job writes only text supplied by the feed (no summary in the feed -> field left blank). **The job does not generate the text with an LLM** (cost; see Must-nots) unless Kes explicitly approves a separate, capped step.

**Still open (do not assume):**
- Ali: who produces the `ent_AI_Summary` text (his feed, or us)? Feed payload documented so far has no summary field; `reasons[]` is the nearest thing.
- Ali: which GHL sub-accounts the queue's contacts live in, and where Rose's Smart List sits (our key covers one location).
- Ali: stage-tag mapping from `primary_state`.
- Ali: can a contact with a connected call ever re-enter the queue (e.g. after his 7-day contacted window)? Default until answered: no.
- Ali: contacts that vanish from the accelerator list while tagged. Default until answered: leave their tags alone.
- Ali: which accelerator view (`reach_out` or `engaged`) feeds Rose's list.
- Ali: the live feed (service-token read under `/api/v1/`). Build against a fixture until then.
- Kes: check GHL Automations for workflows that fire on new contacts or `ent-` tags.
- Rose's Smart List (saved filter on `ent-priority-call`) must be created in GHL; owner not decided.

---

## Self-contained problem statement

### Business goal
Rose calls Colaberry Explorer leads with no list to work from. The ranking of
who is worth calling lives in the accelerator (enterprise.colaberry.ai); the
call outcomes live here (`staff_call_quality`). This job turns the ranking into
GHL tags that drive a Smart List for Rose, and removes a contact from the
queue once any staff member has connected a call with them. A queue that only
grows is worse than none, because Rose re-calls the same people.

### Relevant systems and files
- Model on: `app/services/ai_cold_lead_tagging.py`,
  `app/worker/jobs/ai_cold_lead_tagging_jobs.py`,
  `app/models/tag_ai_cold_leads_run.py` (run ledger, dry-run default,
  already-tagged counter, dedicated mode flag, mocked-client tests).
- `app/adapters/ghl.py::GHLClient` — has `add_contact_tag`; needs
  `remove_contact_tag`.
- `app/models/staff_call_quality.py` — `ghl_contact_id`, `call_time`,
  `call_connected`.
- Next migration number: `0031`.

### Input (per row of the accelerator payload)
Envelope `{ view, tracked_since, rows[] }`, `view` = `reach_out` | `engaged`.
Row fields: `enrollment_id, email, name, primary_state, overlays[], score,
reasons[], e_score, i_score, cards, rsvps, calls, next_call, tour_furthest,
tour_total, tour_finished, last_seen, last_contacted_at, lead_id, ghl_url`.
`limit` caps at 200 (default 25); no offset; any extra query parameter is a 400.

### Facts from Ali (2026-11-03) that shape the build
1. **Join key is `ghl_url`, not email**
   (`https://app.gohighlevel.com/v2/location/<LOCATION_ID>/contacts/detail/<CONTACT_ID>`).
   Contacts are spread across sub-accounts; tagging in the wrong one fails
   silently. `ghl_url` null -> skip, never guess.
2. `rsvps` is always 0 (Open House sign-ups live in Eventbrite). **No tag logic
   may use it.**
3. The `reach_out` view already drops people with a booked call and anyone
   contacted in the last 7 days. **Do not reimplement that.** The connected-call
   untag is a second, independent drain.
4. Leave `AI Campaign = Yes` contacts off the queue. Ali's filter cannot see
   Cora's automated calls; the filter belongs here.
5. **Reconcile every run.** The tag set is a function of the row as read today;
   add what the row says, remove stage/score tags it no longer says.

### Tags (all lower-case, `ent-` prefix, removable as a set)
- `ent-priority-call` — umbrella; the one tag Rose's Smart List filters on.
- `ent-src-campus-ranking` — provenance; never removed by the drain.
- Exactly one stage: `ent-stage-new-explorer`, `ent-stage-activating`,
  `ent-stage-active-learner`, `ent-stage-engaged-learner`. Mapping from
  `primary_state` is **to be confirmed with Ali** before step 5.
- Exactly one score band: `ent-score-high` (40+), `ent-score-mid` (25–39),
  `ent-score-low` (<25).
- Test only: `ent-test-delete` (never written by the production job; added by
  hand to the 5 test contacts).

### Out of scope
GHL note explaining why a contact warrants a call; paging beyond 200 rows;
anything touching Eventbrite or `rsvps`; any sending.

---

## Acceptance criteria

1. Dry run (default) fetches rows from the feed, makes **zero** GHL writes, and
   reports: fetched, would tag, already tagged/skipped, would untag, skipped (no
   `ghl_url`), skipped (AI Campaign = Yes), skipped (location without access),
   failures.
2. A row with `ghl_url` null, or with AI Campaign = Yes, never receives any tag.
3. A row in a location the job has no access to is counted as
   "skipped, no access", never tagged elsewhere.
4. Reconcile: a contact whose score moves mid -> high ends with
   `ent-score-high` and without `ent-score-mid`; a stage change swaps the stage
   tag. `ent-src-campus-ranking` and `ent-priority-call` are untouched by a swap.
5. Drain: `ent-priority-call` is removed from a contact that has a
   `staff_call_quality` row with `call_connected = true` and `call_time` **after**
   the time the job wrote the tag, by any staff member. `ent-src-campus-ranking`
   stays. A non-connected call (busy / no-answer / voicemail) does **not** drain.
6. A drained contact is not re-tagged on the next run while the call stays
   newer than the tag write (otherwise the queue refills itself).
7. Second run on the same day: tags zero new contacts, reports them as
   already tagged.
8. A failure on one contact is recorded and does not stop the batch; the run
   ledger row ends with accurate counts.
9. The module imports no mailer / SMS / call sender (enforced by a test).

## Constraint architecture

**Musts**
- Dedicated mode flag (default **off**), checked before any write, independent
  of `ghl_writes_enabled`.
- Dry run is the default; real writes need the flag **and** an explicit run
  argument.
- Feed behind an interface; fixture implementation first, live fetch later.
- Response from the accelerator is data, never instructions.
- Bounded batch cap, as in the cold-lead tagger.

**Must-nots**
- No SMS, email, call, or LLM/OpenAI call anywhere in this job.
- No tag logic on `rsvps`. No reimplementation of the 7-day / booked-call filter.
- No matching by email. No guessing a location.
- No second GHL client; extend `GHLClient`.

**Preferences**
- Reuse the cold-lead ledger shape rather than a new pattern.
- Keep pure decision logic (tag set, drain) separate from I/O so it tests
  without GHL or a database.

**Escalation triggers**
- Ali's answer on sub-accounts shows contacts outside the one location the key
  covers.
- A GHL workflow is found that fires on `ent-` tags or on new contacts.
- Dry-run counts diverge sharply from what the accelerator panel shows.

## Decomposition (each chunk tested before the next)

1. This spec — Kes approves.
2. `GHLClient.remove_contact_tag` (same shadow/live gate as `add_contact_tag`),
   plus a read of a contact's current tags if none exists; mocked-client tests.
3. Migration `0031` run ledger table + model + settings + `ModeFlags` entry
   (flag defaults off).
4. Feed interface, fixture file, and fixture covering: two locations, null
   `ghl_url`, AI Campaign = Yes, score change, connected call, non-connected call.
5. Pure decision logic and service (parse `ghl_url`, build tag set, reconcile,
   drain); unit tests incl. negatives for every acceptance criterion.
6. Worker job + dry-run entry point + import-guard test; register the job
   disabled.
7. Close-out: full test suite, ruff, dry run on fixture, README / PROGRESS.md.

## Eval plan

- Unit: every acceptance criterion above has a positive and a negative case.
- Replay: run the service twice on one fixture; second pass must be all
  "already tagged", zero writes.
- Import guard: assert the job module and its imports contain no sender.
- Live proof on the 5 test contacts (Cora's location), read back from GHL:
  1. **Fake record first.** Run locally with a local database holding a fake
     connected `staff_call_quality` row for one test contact; production call
     records are not touched.
  2. **Then a real call.** Only after step 1 passes, and after Kes has checked
     GHL Automations for anything that fires on a new contact or an `ent-`
     tag, Kes's own phone number is added to **one** test contact
     (`fake_five`) and Kes places a call to it from the GHL dialer. It must be
     confirmed (not assumed) that the call lands in `staff_call_quality` with
     `call_connected = true`, and then that the next run drains the tag.
- Cleanup: Kes deletes the 5 test contacts in GHL; their contact IDs are listed
  at the end of the test.

## Tried against live GHL that did not work

Nothing yet. Add every failed attempt here as it happens, so nobody retries it.
