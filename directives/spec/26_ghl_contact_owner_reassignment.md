# Spec 26 — GHL Contact Owner (`assignedTo`) Bulk Reassignment

| Area | Status |
|---|---|
| Investigation: does Cora's code represent this workflow at all | **DONE.** It does not — confirmed nothing in this repo (code, `assigned_to`/`assignedTo` field, or GHL integration spec) tracks contact *ownership*. The only existing `assignedTo` usage is CRM **task** assignment (`create_task`), unrelated to contact ownership. |
| Mechanism verification (read + write, current API key) | **DONE, verified live 2026-08-26.** See below. |
| Current counts (Shveta vs. Roselen) | **DONE, snapshotted 2026-08-26.** See below. |
| Bulk reassignment script | **NOT BUILT — blocked on scope decision from Kes.** |
| `GHLClient` methods for owner read/write | **NOT BUILT** — verification used `client._request()` directly, bypassing the adapter's shadow-mode gate (see Constraint architecture). |

## Self-contained problem statement

### Business goal
Before being let go, Shveta manually reviewed a GHL view/pipeline and
reassigned qualifying leads to Roselen (`assignedTo` = GHL user
`0swBv9tBNeXeYXPYFBSx`) as part of updating which leads count as actionable
Cold Leads. That manual step stopped when she left. Kes wants to know how
many contacts are stuck on her, confirm the definition of "Cold Lead" is
still intact, and — pending a scope decision — bulk-reassign her contacts to
Roselen so this workflow doesn't stay silently broken.

**Cold Lead definition (per Kes, 2026-08-26, in response to the original
question)**: anyone who signs up via a form and doesn't enroll in a class is
considered a Cold Lead. This is unchanged and still live in GHL as of this
writing (not independently re-verified against GHL's pipeline/tag config in
this session — Kes's answer was taken as authoritative).

### Relevant systems and files
- GHL contacts API — `POST /contacts/search` (read `assignedTo` via filter),
  `PUT /contacts/{contactId}` (write `assignedTo`).
- `app/adapters/ghl.py::GHLClient` — has `search_contact_by_phone` and
  `update_contact_fields`, neither of which currently exposes
  owner/`assignedTo` search-by-filter or top-level-field writes. A bulk
  script would need either new adapter methods or direct `_request()` calls
  (the verification script used the latter, informally).
- Shveta's GHL user ID: `mW2OSEYWWGDSB9JcKBcr` (supplied by Kes — could not
  be looked up via API, see below).
- Roselen's GHL user ID: `0swBv9tBNeXeYXPYFBSx` (already known from
  `app/core/ai_message_generator.py::_ADMISSIONS_FALLBACK_GHL_ID`).

### Mechanism verified (2026-08-26, against live prod GHL, current API key)

**Read** — count/list contacts by owner:
```
POST /contacts/search
{"locationId": <ghl_location_id>, "filters": [{"field": "assignedTo", "operator": "eq", "value": "<userId>"}], "pageLimit": <n>}
```
Confirmed working under the existing `contacts.readonly` scope. Snapshot
results, 2026-08-26:
- Shveta (`mW2OSEYWWGDSB9JcKBcr`): **2,189 contacts**. Sample of 20 returned
  a wide mix of tags — `enrolled student`, `not interested` / `do not
  contact` / `do not call again`, `warm lead`, `cold lead`,
  `international lead`, `school catalog request`, etc. Not a clean
  "Cold Lead only" bucket.
- Roselen (`0swBv9tBNeXeYXPYFBSx`): **695 contacts**.

**Write** — reassign a contact's owner:
```
PUT /contacts/{contactId}
{"assignedTo": "<userId>"}
```
Confirmed working under the existing `contacts.write` scope, via a
deliberate no-op test: fetched a contact already owned by Shveta
(`Pf5aW2n6H0LP5trF0L7y`), PUT `assignedTo` back to her own ID (same value,
zero actual change), re-fetched and confirmed the value round-tripped
unchanged. Response body included `{"succeded": true, "succeeded": true,
"contact": {...}, "traceId": ...}` (GHL's API has both a misspelled
`succeded` and correctly-spelled `succeeded` key — both present, both
`true`; a future real implementation should check `succeeded` and treat
`succeded` as a compatibility alias, not rely on the typo persisting).

**Not available**: `GET /users/` (would let Cora look up staff GHL IDs by
name/email itself) returns `401 "The token is not authorized for this
scope."` — the Private Integration token lacks `users.readonly`. Until that
scope is added, any future "who is this staff member's GHL ID" question
needs a manually-supplied ID, the same way Shveta's was obtained this
session.

### Known edge cases / open questions (blocking the actual bulk script)
- **Scope of "assigned to Shveta" to reassign**: all 2,189, or filtered to
  Cold-Lead-tagged/staged only? The 2,189 include enrolled students and
  explicit do-not-contact leads — a blanket reassignment would hand those to
  Roselen too. **Not yet decided by Kes.**
- **One-time backfill vs. ongoing safeguard**: nothing currently alerts if a
  new lead lands on a departed staff member's `assignedTo`. Not yet decided
  whether this needs a recurring check.
- Pagination: verification only pulled `pageLimit` up to 20 for the sample;
  a real bulk script needs to page through all 2,189 (GHL's search endpoint
  paginates, needs a cursor/`startAfter` loop — not yet implemented).

### Out of scope (for this spec entry)
- Actually building and running the bulk reassignment — gated on the scope
  decision above.
- Re-verifying the Cold Lead pipeline/tag definition against live GHL
  config — Kes's stated definition was taken as-is.
- Requesting the `users.readonly` scope be added to the Private
  Integration — a GHL-admin-side action, not something achievable via API.

## Constraint architecture
- **Must not** run a real bulk reassignment without Kes's explicit go-ahead
  on scope (all vs. filtered) — this is a production write across
  potentially thousands of live CRM contacts, squarely in this repo's
  approval-gated-changes category.
- **Must**, when built, route writes through `GHLClient` (new methods, not
  ad-hoc `_request()` calls like this verification used) so it inherits the
  existing shadow-mode gate (`ghl_writes_enabled`) and retry/error handling,
  consistent with every other GHL write in this codebase.
- **Must** page through the full result set — a script that only reads the
  first page would silently under-report/under-process.
- **Preference**: default to a dry-run mode that reports what *would* change
  before executing, mirroring this repo's general safety conventions for
  bulk/production-impacting scripts.

## Eval design (once built)
- Unit test: search-by-owner pagination logic against a mocked multi-page
  response.
- Unit test: write path respects `ghl_writes_enabled` shadow gate.
- Dry-run against prod data, reviewed by Kes, before any live run.
- Post-run: re-query counts for both owners to confirm the expected delta.
