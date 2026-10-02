# Spec 33 — Voicemail cadence, daily call cap, and real-SMS routing

| Area | Status (2026-10-01) |
|---|---|
| Slot rebalancer no longer pulls calls earlier (`app/worker/jobs/slot_rebalancer.py`) | **BUILT + tested locally. NOT deployed.** |
| Hard cap: 2 calls / lead / local day, callbacks exempt (`app/core/call_policy.py`, `outbound_jobs.daily_cap_deferral`) | **BUILT + tested locally. NOT deployed.** |
| "SMS" follow-ups delivered as real SMS (`app/core/followup_routing.py`, `crm_jobs.update_ghl_after_vm_message`) | **BUILT + tested locally. NOT deployed.** |
| TCPA 08:00–21:00 local floor for Cora's real SMS (`channel_jobs._check_active_window(tcpa=True)`) | **BUILT + tested locally. NOT deployed.** |
| Classification tag no longer written to Support issue Ticket #4 | **BUILT. URGENT to deploy — see "Live hazard".** |
| Links in SMS: only `myfreeaiclass.com`; any other link -> delivered as an EMAIL (`app/core/sms_links.py`) | **BUILT + tested locally. NOT deployed.** |

## Required cadence (Settings page, verified saved in `app_config` 2026-10-01)
| Missed call | Cold Lead delay to next call | New Lead delay to next call | Text | Email |
|---|---|---|---|---|
| 1st (none→0) | 120 min | 120 min | yes (+2 min) | no |
| 2nd (0→1) | 2,880 min | 1,440 min | yes (+2 min) | **yes, same time** |
| 3rd (1→2) | 2,880 min | 2,880 min | yes (+2 min) | no |
| 4th (2→3) | final | final | no | no (AI Campaign = No) |

Plus: **max 2 calls per lead per local day** (lead's timezone) unless the lead asked for a call back.

## Findings (why leads got 3–4 messages a day)
1. **Rebalancer compaction.** `rebalance_call_slots` ran every 5 min and, whenever ANY 5-minute slot held more than 4 pending calls, re-ranked EVERY pending future call into consecutive slots starting from now (4 per 5 min). Calls configured for 24/48 h were pulled forward to ≈ queue length ÷ 48 per hour. Measured 2026-09-21→30: Cold retries configured 2,880 min ran after a median 277 min (4.6 h); **0 of ~2,900** kept 48 h; New Lead 1,440 → ~5 h. Queue sizes at sample moments (261/216/187/130) × 75 s predict the 270 min seen. (Server logs only reach back hours, so the repack events themselves could not be shown; no repack in the 24 h before the fix.)
2. **"SMS" was delivered as an email.** Cora wrote SMS text to Support Issue Ticket #2, which starts GHL "AI Agent - Send Email". So every missed call produced an email, plus the real email on the 2nd miss (two similar emails seconds apart).
3. **Some leads were called far more than 4 times** (28 leads 5×, others 7×/15×/21×) — GHL re-enrollment and unbounded no-answer retries; the daily cap bounds this to 2/day.

## Changes
- **Rebalancer invariant:** a call's `run_at` is **never moved earlier**, and a call in a slot within capacity is never touched. Only the EXCESS of an over-full slot moves, to the earliest later slot with room (75 s offsets). Lead-requested callbacks (`intent_reason` in `callback_request`, `callback_with_time`, `call_later_no_time`, `transfer_requested`) hold their slot and are immovable. Planner is a pure function (`plan_redistribution`); applied with version-checked updates under the same advisory lock the schedulers use.
- **Daily cap:** in `launch_outbound_call_job`, after the window check and before dialing: count this lead's COMPLETED launch jobs since local midnight (matching `entity_id` or payload `contact_id`); at ≥ `max_calls_per_lead_per_day` (default **2**, `app_config`, `0` disables) the job is cancelled and re-scheduled to the campaign-window open of the lead's NEXT local day. Lead-requested callbacks are exempt. Not applied in shadow mode.
- **Routing (what the GHL write is):** `email` → Support Issue Ticket #2 = subject + Message = body → "AI Agent - Send Email". `sms` → **Support issue Ticket #4 = the text** → "AI Agent - Send SMS"; Ticket #2 untouched so the email workflow does not fire. SMS is skipped (reason logged, rest of the update still written) when the live GHL contact is unreadable, has no phone, is DND (all channels / SMS), or carries an opt-out tag.
- **Rollback switch:** `app_config sms_delivery_mode` = `email` restores the legacy behaviour (SMS text → Ticket #2 → emailed) with no deploy. Default (absent) = real SMS.
- **Ticket #4 is no longer a classification field.** It is the SMS body; anything written there is texted.
- **TCPA:** `send_sms_job` also requires 08:00–21:00 in the lead's timezone even when the campaign window is wider (New Lead runs to 22:00); otherwise it re-schedules to 08:00 local.

## Links in SMS (Kes, 2026-10-01 — final decision: NO links at all)
- **No link of any kind goes out in an SMS — not even `myfreeaiclass.com`.** The allow-list `app_config sms_allowed_link_domains` is **empty by default** (comma-separated domains re-allow specific sites later, e.g. `myfreeaiclass.com` once the A2P campaign is confirmed to allow embedded links; no deploy needed).
- A follow-up written as "sms" whose text carries ANY link (Eventbrite RSVP, YouTube, our own site, other domains, shorteners such as bit.ly) is delivered as an **EMAIL** instead: Ticket #2 = short subject (first meaningful sentence of the text, link-free), Message = small HTML body with the link clickable and the "Text STOP" line removed (GHL appends the unsubscribe footer). Ticket #4 is not touched, so nothing is texted. If the lead cannot be emailed (no address / Email DND / unreadable) nothing is sent.
- Email addresses are not links. Detection: `http(s)://`, `www.`, and bare domains on common TLDs (`app/core/sms_links.py`).
- The correction SMS (switched off) carries dates only; the AI prompt override for expired dates tells the model to put the free-signup address ONLY in the email and just invite a reply in the SMS.
- Last 7 days (Cora-recorded SMS): 45 of 1,719 (3%) carried a link and would have gone by email (more once our own site counts).

## What happens to work already queued when this is deployed (checked 2026-10-01 04:10 UTC)
| Item | State now | After deploy |
|---|---|---|
| Pending `launch_outbound_call` | 167 jobs, 10-01 17:10 -> 10-02 19:35 (Chicago); no lead has 2 queued | **Stay exactly where they are.** The rebalancer will never move them earlier; the 2/day cap is checked when each one runs (a lead already called twice that local day is moved to the next day's window; callbacks exempt) |
| Pending `send_sms` / `send_email` / `update_ghl_after_vm_message` | 0 (they are created ~30 min after a call and run immediately) | The first new ones (after the next calls) use the new routing |
| Messages already sent / recorded | history | Unchanged; nothing is re-sent |
| Calls already pulled forward by the old rebalancer | done | Cannot be undone; new retries use the full configured delays |
| Open leads mid-cycle | each has at most one queued retry | Their next retry keeps its delay; later retries use 120 / 1,440-or-2,880 / 2,880 |

## Live hazard fixed by this change (until deployed it is still live)
Since the SMS workflow trigger was corrected on 2026-10-01 (~01:30 UTC), the OLD `update_ghl_after_vm_message` still writes the lead's classification tag (e.g. `warm_lead`) into Ticket #4 when one exists → the SMS workflow would text the lead that word. No such SMS had been sent as of 03:56 UTC (no update jobs ran overnight). It can happen as soon as calls resume the next morning. Anything else that writes Ticket #4 (the Synthflow post-call recap) is now also sent as an SMS; legacy values there carry old dates (1/31, 3/28, 5/30/2026).

## Known risks / open items
- SMS volume becomes real (≈ 1,000–2,000/week): check the A2P 10DLC trust score and daily cap; SMS prompts may include long URLs (carrier filtering) — prompts unchanged.
- A lead whose SMS is skipped (DND / no phone) receives nothing for that touch; the outbound_messages row is already recorded.
- Calls already pulled forward cannot be un-pulled; new retries use the full delays.
- GHL-native automations and the Synthflow recap SMS are outside Cora's control.

## Tests
`tests/unit/test_cadence_fixes.py` (28): planner never earlier / only excess moves / far-future untouched / callbacks immovable / idempotent / DB apply; cap under/at/exempt/other-day/cancelled/local-midnight/entity-or-payload/config/zero; routing email vs sms vs legacy, classification never written, DND/opt-out/no-phone/unreadable skips; SMS 21:30 deferred to 08:00 next day, 14:00 proceeds, email unaffected.

## Verification after deploy
```sql
-- retries must now keep their configured delay (compare actual to cfg)
SELECT payload_json->>'campaign_name' campaign, payload_json->>'vm_retry_attempt' attempt, payload_json->>'delay_minutes' cfg_min,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (run_at-created_at))/60)) median_actual_min, count(*)
FROM scheduled_jobs WHERE job_type='launch_outbound_call' AND created_at > now()-interval '2 days' AND coalesce(payload_json->>'source','')=''
GROUP BY 1,2,3 ORDER BY 1,2;
-- no lead dialed more than twice per local day (callbacks excluded)
SELECT entity_id, count(*) FROM scheduled_jobs WHERE job_type='launch_outbound_call' AND status='completed'
  AND updated_at >= date_trunc('day', now() AT TIME ZONE 'America/Chicago') AT TIME ZONE 'America/Chicago'
  AND coalesce(payload_json->>'intent_reason','') NOT IN ('callback_request','callback_with_time','call_later_no_time','transfer_requested')
GROUP BY 1 HAVING count(*) > 2;
```
