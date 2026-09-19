# Spec 31 — AI Cold Lead Tagging (daily batch job + dashboard tile)

| Area | Status |
|---|---|
| GHL `/contacts/search` filter semantics for all 5 criteria | **VERIFIED LIVE 2026-09-18** against production, read-only. |
| Pagination cursor (`searchAfter`) | **VERIFIED LIVE 2026-09-18** — confirmed round-trip, zero page overlap. |
| Adapter methods, service module, worker job, migration, dashboard tile | **IMPLEMENTED, deployed 2026-09-18** (`329bdc5`, fix `3e03b6d`). |
| 5-contact canary (real GHL writes, manual one-off, `batch_cap=5`) | **DONE 2026-09-19** — verified independently in GHL; all 5 tagged correctly, 0 failures. |
| `ai_cold_lead_tagging_enabled` | **LIVE as of 2026-09-19** (flipped via `/dashboard/mode`, operator `claude-code`, approved by Kes after canary review). Daily job now runs for real, capped at `ai_cold_lead_tagging_batch_cap=500`/cycle. |

---

## Self-contained problem statement

### Business goal
Contacts matching a specific staleness/eligibility filter should be tagged
`ai cold leads` in GHL automatically, once per day, so staff can work them
as a distinct queue. Kes needs a dashboard tile showing how many contacts
were tagged recently (activity) and how many currently-qualifying contacts
are still untagged (backlog).

### Relevant systems and files
- GHL contacts API — `POST /contacts/search` (read, paginated),
  `POST /contacts/{contactId}/tags` (write — not yet verified live, planned
  for the adapter implementation step; `contacts.write` scope already
  covers analogous writes per `directives/spec/16` and `26`).
- `app/adapters/ghl.py::GHLClient` — Private Integration client, no tag
  methods exist yet.
- This location: `ttBtJmxoLwjf18lIvvVD`, **15,693 total contacts** as of
  2026-09-18.

### Out of scope
- The GHL Marketplace OAuth app (`ghl_conversations.py`) — unrelated
  Conversations/call-recording feature, not touched by this work.

---

## Mechanism verified (2026-09-18, against live prod GHL, current Private Integration key, read-only)

Script: `execution/test_scripts/verify_ai_cold_lead_filters.py` (kept in
the repo as a reusable verification/debugging tool, same convention as
`test_ghl_writes.py`).

**Confirmed filter contract** — `POST /contacts/search` accepts a
`filters` array of `{"field", "operator", "value"}` objects, ANDed
together. Allowed operators (from a live 422 error listing them):
`eq, not_eq, contains, not_contains, wildcard, not_wildcard, match,
not_match, exists, not_exists, range, not_range, contains_set,
contains_not_set, gt, gte, lt, lte, nested, nested_not, has_child,
has_parent` — but **not every operator works on every field** (date
fields reject `lt`/`gt`/`gte`/`lte` with a 422 despite being in this
generic list; only `range` works on `dateUpdated`/`lastActivity`).

| Criterion (from dashboard UI filter) | Confirmed GHL filter | Live-tested result |
|---|---|---|
| Contact type = Lead | `{"field": "type", "operator": "eq", "value": "lead"}` | 13,774 / 15,693 |
| DND all — Disabled | `{"field": "dnd", "operator": "eq", "value": false}` | 15,209 / 15,693 |
| Phone not empty **and** contains +1 | `{"field": "phone", "operator": "wildcard", "value": "+1*"}` — a single wildcard filter satisfies both UI rows at once (a contact can't match `+1*` with an empty phone) | 11,350 / 15,693 |
| Tag NOT IN [10 exclusion tags] | `{"field": "tags", "operator": "not_contains", "value": [<10 tags>]}` — a single filter takes an **array** value; confirmed identical result to 10 separately-ANDed `not_contains` filters (7,852 either way) | 7,852 / 15,693 |
| Last activity > 30 days ago | `{"field": "lastActivity", "operator": "range", "value": {"gte": 0, "lte": <epoch_ms_cutoff>}}` — a real `lastActivity` field exists on `/contacts/search` results (epoch-ms ISO string) and is a better match for the UI's "Last activity" filter than the `dateUpdated` proxy originally guessed | 12,611 / 15,693 |
| **All 5 combined (AND)** | see below | **1,591 contacts** |

**Combined filter** (the exact production query the daily job will run):
```json
{
  "locationId": "ttBtJmxoLwjf18lIvvVD",
  "filters": [
    {"field": "type", "operator": "eq", "value": "lead"},
    {"field": "dnd", "operator": "eq", "value": false},
    {"field": "phone", "operator": "wildcard", "value": "+1*"},
    {"field": "tags", "operator": "not_contains", "value": [
      "business lead", "colaberry employee", "invalid phone number",
      "not a lead", "spam", "marketing contact", "international lead",
      "warm lead", "ai cold leads", "ai cold leads ii"
    ]},
    {"field": "lastActivity", "operator": "range", "value": {"gte": 0, "lte": <now_minus_30d_epoch_ms>}}
  ],
  "pageLimit": 100
}
```
**Result: 1,591 contacts currently match** — this is the real, current
backlog size the first live run would tag. **Kes should sanity-check this
number against the same filter applied in GHL's own UI** before the
feature ever goes live (per the approved plan's verification step 3) —
it's meaningfully larger than the "a few hundred" earlier assumed during
scoping, which affects the `ai_cold_lead_tagging_batch_cap` default.

**Pagination** — confirmed live: each contact object returned by
`/contacts/search` carries its own `searchAfter: [epoch_ms, contact_id]`
array. Passing `{"searchAfter": [epoch_ms, contact_id]}` (from the last
contact of the current page) in the next request's body returns the next
page with zero ID overlap with the previous page — verified directly.

**Rejected/invalid attempts, for the record** (so nobody re-tries these):
- `contactType` field name — 400 "Invalid field contactType" (use `type`).
- `phone` `exists`/`not_eq`/plain `contains` with a 2-char value — all
  fail (min. 3 chars for `contains`; `exists` needs no boolean/string
  value GHL will accept; `not_eq ""` silently no-ops). `wildcard "+1*"` is
  the correct approach and subsumes "not empty" for free.
- `dateUpdated`/`dateAdded` with `lt`/`lte`/`gte` — 422 "Invalid Operator
  ... passed for field date_updated" even though those operators are in
  the generic allowed list. Only `range` works on date fields.
- `date_updated` (snake_case field name) — 400 "Invalid field
  date_updated" (GHL's own error message renders the internal name in
  snake_case, which is misleading — the filter `field` value itself must
  stay camelCase `dateUpdated`).

---

## Constraint architecture

**Musts**
- The daily job must page through all matches via `searchAfter`, not
  assume everything fits on one page (1,591 today, will grow).
- The exclusion-tag list must be a single `not_contains` filter with an
  array value (proven equivalent to 10 chained filters, far cheaper).
- `ai_cold_lead_tagging_enabled` (dedicated flag, off by default) must
  gate the run before any `add_contact_tag` call, independent of the
  shared `ghl_writes_enabled`/`ghl_write_mode` gate.

**Must-nots**
- Must not use `dateUpdated` as the "last activity" field now that a real
  `lastActivity` field is confirmed to exist and behave correctly.
- Must not assume any operator from the generic allowed-operators list
  works on any given field without live-testing it first — this location's
  GHL instance rejects several combinations that are technically listed.

**Preferences**
- Reuse the array-value `not_contains` form over chained filters (simpler
  payload, identical result, confirmed).

**Escalation triggers**
- If a future live run's `contacts_scanned` count diverges significantly
  from what GHL's own UI reports for the same filter, stop and
  re-verify — don't assume the filter contract silently changed correctly.

---

## Decomposition status
Step 1 (this spike) — **done**. Steps 2–9 per the approved implementation
plan (`app/adapters/ghl.py` methods, settings/`ModeFlags` wiring, migration
`0023`, `app/services/ai_cold_lead_tagging.py`, worker job registration,
dashboard read path, frontend tile, tests) — not yet started.

## Eval plan
See the approved plan's "Tests" section — adapter-level, service-level,
worker-job-lifecycle, and dashboard-metrics cases are enumerated there and
will be implemented alongside each corresponding code change, not deferred.
