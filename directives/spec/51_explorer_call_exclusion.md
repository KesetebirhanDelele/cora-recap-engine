# Spec 51 - Explorer sign-ups are never dialed by Cora's AI campaigns

Status: merged and deployed 2026-11-21 (2c71754).

## Problem
Explorer sign-ups (Ali's accelerator list) are worked by Rose and Taiwo. GHL's "Contact Created" workflow still sends every new lead with a phone to Cora's New Lead campaign, and the Cold Lead tag workflows re-send people every 45 days. Measured 2026-11-21: all 100 checkable people on Rose's first list had been dialed by Cora's AI (219 calls, 3 reached), the first call usually within hours of sign-up.

## Rule
A contact carrying a tag in `explorer_call_exclusion_tags` (default `ai explorer,ai-explorer`, case-insensitive, exact match; `ai-explorer` is the hyphenated tag Ali's sign-up code can write in the contact-create call, asked 2026-11-22) is never entered into an AI outbound campaign and never dialed. Switch: `explorer_call_exclusion_enabled` (default true).

## Where
- `app/core/explorer_guard.py` `check_is_explorer(phone, settings)`: reads the GHL contact by phone, returns `{ghl_contact_id, matched_tags}` or None. Fails OPEN (GHL problem -> campaign proceeds), like the student guard.
- `enter_campaign()` (campaigns.py): skips entry, records skip reason `explorer_excluded`.
- `launch_outbound_call_job()`: checked again at dial time (the tag can land after GHL sends the webhook; the 45-day cycle re-sends people) and cancels the job. No dashboard exception: expected suppression.

## Must / Must-not
- Must: refuse at both doors; keep the tag list in settings, not in code.
- Must not: block people who only carry `ent-` queue tags, or any tag that is not in the list.
- Escalation: if the tag turns out to be added only after enrollment is complete (Ali to confirm), the dial-time check is the one that matters; consider also checking membership in Ali's feed.

## Tests
`tests/unit/test_explorer_guard.py` (tag matching, unknown contact, switch off, GHL failure fails open, real defaults); `test_campaigns.py` (entry skipped, settings None); `test_outbound_jobs.py` (dial cancelled, nothing placed, no exception).

## Known limits
- Two extra GHL reads per dial (same as the student guard).
- 10 of the 120 people on Rose's first list lack the `ai explorer` tag, so they are not covered by the tag rule.
- GHL still sets AI Campaign = Yes on new contacts; those people stay off Rose's list while it says Yes. The GHL-side branch (skip `ai explorer`) is still wanted.
