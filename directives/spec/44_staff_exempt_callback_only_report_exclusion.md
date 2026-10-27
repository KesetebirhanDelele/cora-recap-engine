# 44 - All staff exempt from the "free" / old-course audit; dead-number calls out of the reports (and the HELD callback-only change)

**Status:** sections 1 and 3 built, tested and deployed 2026-10-26. Section 5 (callback-only) is BUILT AND TESTED BUT HELD at Kes's request ("leave #5 for now"): it is NOT in this release; it lives on branch `hold/callback-only-teams` (this release plus the held change).

## 1. Every staff member is exempt from the "free" / old-course audit
Spec 35 exempted only the people on the admissions and IPBC dashboard lists (Roselen; Taiwo was never saved). Now `content_audit.staff_user_ids` builds the staff set
from: `admissions_assistants`, `ipbc_assistants`, the whole `support_staff_roster`, plus `audit_staff_user_ids` (comma list in dashboard config; default =
Roselen `0swBv9tBNeXeYXPYFBSx` and Taiwo `93bhNRgb5pzSoHmaSimH`, the ids task assignment already falls back to), minus `audit_ai_user_ids`.

- **The AI id is never staff.** The Conversation AI and the workflows post under `yIhCTptvoNLixaWkLcRd`, which is ALSO a real person on the support roster
  (Balakrishna). Checked 2026-10-26: an AI text and a typed text have identical keys and the same source `app`; nothing marks the AI. Exempting that id would
  exempt the AI, so Balakrishna's own texts are still audited. To fix this properly, give the Conversation AI its own GHL user.
- Unchanged: only source `app` counts (workflows never), "free" and course names are recorded as `staff_seen` and not alerted, an employment / placement claim from
  staff is still flagged, malformed lists contribute nothing, clearing `audit_staff_user_ids` removes the defaults.

## 3. Dead-number calls are not reported
A call answered by a carrier / phone-system recording (spec/41) is not a conversation. When the intent is `carrier_message`, `run_call_analysis` now sets
`call_events.report_excluded = true`, the existing flag that silences a row in Voice Performance and the AI reports. `execution/fix_carrier_message_calls.py`
also sets it on the existing calls (dry run lists the count; `--apply` writes; idempotent). Not covered: Delivery Health still counts these calls (it does not
use `report_excluded`).

## 5. Admissions and Support are callback-only - HELD, NOT DEPLOYED (branch `hold/callback-only-teams`)
Synthflow cannot transfer a live call to numbers outside the US, so in four failed transfers on 2026-10-26 (3 to Roselen `transfer_blocked_by_carrier`, 1 to support
`destination_unavailable`) the transfer action played its OWN fixed line, "Sorry, I couldn't transfer you. Would you like to continue the conversation with me or try
again later?", the caller answered "later" and the agent said goodbye without booking (booked on 1 of 4). That line comes from the Synthflow transfer action, not from
our prompts, so the fix is to never call the transfer for those teams:
- `staff_availability.availability_block` (the LIVE STATUS block on every outbound call): teams in `transfer_callback_only_teams` (dashboard config, default
  `admissions,support`; `ipbc` can be added, an empty value turns it off, e.g. when Ali re-enables transfers) are shown as "NO live transfer (callback only)" with when the
  team can call back; only Payments / IPBC (Taiwo, US) keeps AVAILABLE NOW / NOT available.
- Prompts (cold and warm injected per call; inbound pasted by Kes): Section 0 item 1 now says live transfer is only for Payments / IPBC, Admissions and Support are
  booked as a callback at once; item 3 books the callback; item 4 forbids "do you want to keep talking to me", "I can keep helping you instead", "stay on the line" and
  any choice between staying with the agent and a callback. Section 7 routing updated.
- The failed-transfer alert (spec/39) and the "CALL BACK NOW" task are unchanged and still cover a failed Payments transfer.

## Acceptance
- A roster member, Taiwo and Roselen's texts with "free" or an old course name are recorded as `staff_seen`, not flagged; the AI user id, an unknown user and a workflow
  are flagged; an employment claim from staff is flagged.
- A carrier-recording call is excluded from the reports automatically; the cleanup excludes the existing ones and a second run does nothing.
- (Held with section 5) By default the LIVE STATUS block never offers a live transfer to Admissions or Support; config can switch a team back.

## Tests
`test_content_audit_exemptions.py` (+7), `test_carrier_message_loop.py` (+1). The held section 5 adds 7 tests to `test_staff_availability.py` on its own branch.
