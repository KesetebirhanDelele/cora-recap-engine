# 36 — Opt-outs: lead wording -> real GHL DND (calls, SMS replies, email replies)

**Status:** built and tested 2026-10-02; deployed with commit noted in PROGRESS.md.

## Problem
Cora's call check (`intent_detection`, regex only - no LLM) marks a lead `do_not_call` in Cora and writes `AI Campaign = No` to GHL, but **never set GHL's native DND**. A live check found 16 of the newest 40 Cora do-not-call leads (40 %) had no DND in GHL; 3 of 4 free-form email opt-outs and 1 of 1 free-form SMS opt-out also had none (only exact `STOP` SMS replies get DND from GHL itself). Replies were not read at all (`inbound_messages` was empty until spec/35).

## Behaviour (Kes, 2026-10-02: scope by the lead's wording; clear -> automatic; unclear -> review; reconcile with a list first)
| Source | Trigger | Action |
|---|---|---|
| Answered call | Cora's `do_not_call` intent AND opt-out wording in the **lead's own lines** (`human:`), not the bot's | GHL DND for the channels the lead named; Cora `do_not_call` + pending calls cancelled |
| SMS / email reply | read by `delivery_sync` (spec/35), quoted history and our footers stripped | same; exact `STOP` / `unsubscribe` = the channel it arrived on |
| Free-form / hostile / legal wording | regex "unclear" | LLM judge (gpt-4o-mini): `opt_out_*` with confidence >= 0.85 -> applied (decided_by `llm`); lower or no LLM -> **review list** |
| "not interested" / "wrong number" (short replies) | regex | lead closed like the call path (AI Campaign = No); long replies -> review |

Scope rules (`app/core/optout.py`): stop calling -> `call`; stop texting / no more texts -> `sms`; stop emailing -> `email`; stop messaging -> `sms + email`; remove me / unsubscribe / opt out / leave me alone / do not contact / take me off the list -> all three (also sets the contact-level `dnd`). Combined wording unions ("don't call or text me" -> call + sms). Out-of-office replies, "don't stop", "stop by" are ignored.

GHL payload (verified on Kes's own contact 2026-10-02): `PUT /contacts/{id}` `{"dndSettings": {"SMS": {"status": "active", "message": ..., "code": "cora_optout"}}}`; all channels also `"dnd": true`. Undo = status `inactive` (and `dnd: false`), only for channels Cora turned on.

## Cora's own sends respect it
`update_ghl_after_vm_message` (follow-up SMS/email) asks `optout.cora_block_reason` first: Cora do-not-contact / closed / invalid, a STOP reply in `inbound_messages`, an applied opt-out covering the channel, or one **awaiting review** (conservative) -> the send-trigger fields are not written. (Cora's own `do_not_call` still blocks every channel, as before - GHL DND is the part scoped by wording.)

## Reconciliation
Each metrics cycle (60 s, 15 leads) `reconcile_step` compares every Cora `do_not_call` lead with GHL: already DND -> `ok`; missing -> a review row whose scope comes from the lead's words on the call (all channels when none are on file). Nothing is applied until the operator approves (per item or "Apply DND to all" on the tile).

## Tile "Opt-outs & DND" (`/optouts`)
Counters (waiting / applied / already DND / still checking); **Needs your decision** (excerpt, proposed channels as checkboxes, Apply DND / Not an opt-out); **Marked do-not-call in Cora, no DND in GHL** with bulk apply; **Recently applied** with Undo. Home tile badge = items waiting.

## Data
`optout_actions` (migration 0028): source (call | sms_reply | email_reply | reconcile), external_id (unique per source - replays never write twice), kind, scope, confidence, decided_by (auto | llm | operator), status (applied | review | ok | dismissed | failed | undone | shadow), phrase, 300-char excerpt, previous GHL/lead state for undo. Every change also writes `audit_log` (`optout_*`).

## app_config
`optout_llm_enabled` (true) · `optout_reconcile_enabled` (true). Shadow mode (GHL writes off) records `shadow` rows and writes nothing.

## Fixes found live (2026-10-02)
* GHL refuses to overwrite a **permanent** DND (what an earlier STOP leaves): `Not authorized to update permanent dnd setting for SMS`. Cora now writes only the channels that are not already DND (nothing at all when everything is covered).
* The reply sync passed the conversations-scoped token to the DND write (`The token is not authorized for this scope`). The write now always uses the contacts token.
* Automatic writes that fail are retried every metrics cycle (max 5 attempts, `retry_failed`); an operator click that fails stays on the tile as `apply failed: ...` and is excluded from "Apply to all".

## Limits
* Voicemail messages are not transcribed for opt-outs (calls only when answered, as before).
* Cora's call regex still runs on the whole transcript; DND is applied only when the wording is in the lead's own lines, else it goes to review.
* The LLM sees at most 600 characters of the reply, at most 8 judgements per sync run.

## Verification
```sql
SELECT source, kind, scope, status, decided_by, count(*) FROM optout_actions GROUP BY 1,2,3,4,5 ORDER BY 6 DESC;
SELECT created_at, source, scope, left(excerpt,80) FROM optout_actions WHERE status='review' ORDER BY created_at DESC LIMIT 20;
```
Tests: `tests/unit/test_optout.py` (wording matrix + DB-backed apply / idempotency / LLM / review / undo / reconcile / Cora-side block).
