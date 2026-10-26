# 43 - "AI Campaign = No" must really reach GHL for opted-out and invalid leads

**Status:** built and tested 2026-10-26 on `fix/ai-campaign-off-write`; deploy and backfill dry run follow Kes's go-ahead.

## Problem
`AI Campaign` is a Yes/No custom field on each GHL contact: the switch for the automated follow-up that runs inside GHL. Cora writes Yes when it
starts or continues follow-up (`followup_routing`, post-call analysis) and No when follow-up ends (finalization) or the lead opts out. Spec 01
(line 65) and spec 06 require the opt-out handlers (`do_not_call`, `not_interested`, `wrong_number`) to write `AI Campaign = No` to GHL "stopping GHL
automations for that contact".

The opt-out write never reached GHL:
1. `_write_ghl_campaign_off` (since 2026-04-04) called `GHLClient.update_contact_fields` WITHOUT the dashboard's live-mode flags. The client then
   falls back to the env setting `ghl_writes_enabled`, which is False in production, so the write ran in shadow mode and did nothing.
2. Most leads are keyed by phone number, and GHL cannot update a contact by phone number.
3. The function logged "written" even when it was a shadow write, and its tests replaced the write with a stand-in, so nothing noticed.

Evidence (2026-10-26, read-only): of the 60 most recent leads Cora marked do-not-call or invalid, GHL still said Yes for 49 (48 of 54 do-not-call,
1 of 6 invalid). The 2026-10-26 carrier-recording cleanup (spec/41) reused the same helper, so its "6 numbers marked invalid" changed Cora's
lead_state only; one of them (+16174851825, New Lead) still said Yes.

## Behaviour now
- `intent_actions.set_ai_campaign_no(ghl, contact_id, settings, flags, reason)`: resolves the GHL contact, resolves the field id, writes `No` with the
  live flags, and returns True ONLY when GHL accepted a real write. Shadow mode, an unmatched contact or a missing field return False and log at
  WARNING. It never claims "written" for a shadow write.
- `resolve_ghl_contact_id`: a real GHL id is used as is; a phone-keyed id is looked up in GHL and accepted ONLY when the found contact's phone number
  really matches (last 10 digits), so GHL's fuzzy text search can never send a write to the wrong person.
- `_write_ghl_campaign_off(contact_id, settings, reason, session)` is the wrapper the three handlers call (they now pass `session`, so the dashboard's
  live-mode flags apply: System Controls in shadow = no writes, by design). Still non-fatal to the lead_state update that already ran, never raises.

## Backfill
`execution/backfill_ai_campaign_off.py` (dry run by default): every `lead_state` row with `do_not_call` or `invalid`, matched to its GHL contact,
skipping those already No and those GHL cannot match. `--apply` writes No through `set_ai_campaign_no` with the live flags, then READS EACH CONTACT
BACK from GHL and reports mismatches; it refuses to run while GHL writes are in shadow mode. Idempotent; a lead that never opted out is never touched.

## Acceptance
- Live flags + matching contact: GHL receives `{AI Campaign: No}` for the right GHL id and reads back No.
- Shadow flags, no flags, near-match contact, missing field: nothing is written and the function returns False.
- The three handlers pass the session; the wrapper never raises.
- Backfill dry run lists only contacts not already No; apply writes only those and verifies each; a second run does nothing.

## Not changed / watch
- GHL's own Do-Not-Disturb is a separate mechanism (`optout_actions`, the Opt-outs page); this spec only fixes the AI Campaign field.
- I have not inspected which GHL workflows key on AI Campaign; the spec text says they stop when it is No. Confirm in GHL.
- Leads whose contact cannot be matched by phone in GHL are listed by the dry run and never written.

## Tests
`tests/unit/test_ai_campaign_off_write.py` (19): uses a fake GHL that is shadow unless the live flags are passed, returns a fuzzy near-match on phone
search and supports read-back - resolver cases, live vs shadow vs missing-field vs unmatched, handler wiring, wrapper, and the backfill (dry run,
apply with read-back, idempotency, shadow refusal).
