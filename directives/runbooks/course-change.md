# Runbook: the course, price, date or link changes

Use this every time the offer changes (new course, retired course, price, schedule, link, Open House). The reason it exists: when the program
changed in 2026 the offer was updated in the places we knew about, and an AI agent inside GHL kept describing the retired course and a
scholarship, a $1,000 tuition and a 71% employment rate for weeks because its prompt was on nobody's list. The list of places is
`directives/spec/38_offer_single_source_roadmap.md` section 3; **this checklist is the procedure that uses it. Update both together.**

## 0. Rules that apply to every message, on every channel, from anything
- **Never state an employment rate, placement rate, job guarantee or salary figure** unless Ali has approved the number and its source. (Cora's own messages: blocked in code by `app/core/offer.py` forbidden terms.)
- **The word "free"**: Texas Workforce Commission rules need written approval for it (Ali, 2026-10-03). Follow Ali's decision on scope and approved wording; until decided, treat new uses of "free" as needing his sign-off.
- No scholarship, discount or financing claim that is not in `docs/colaberry-knowledge-base.md`.
- Only the current program is offered. Retired course names are listed in `offer_forbidden_terms` and must be updated whenever a course is retired.

## 1. Decide the facts (owner: Kes, approvals: Ali)
Name, what it is, schedule, plans and prices, extra costs, eligibility, links, the next Open House date and class start (Settings page). Write them into
`docs/colaberry-knowledge-base.md` first. Everything below is copied from it.

## 2. Update every place (tick each; owner in brackets)
| # | Place | How | Owner |
|---|---|---|---|
| 1 | `docs/colaberry-knowledge-base.md` (the source of truth) | edit, commit | Claude |
| 2 | `app/core/offer.py` defaults and `app_config` keys `offer_name`, `offer_facts`, `offer_forbidden_terms` | edit/Settings; add the retired course names to the forbidden terms | Claude |
| 3 | Voice prompts `docs/synthflow-cold-lead-prompt.md`, `synthflow-warm-lead-prompt.md` (loaded into Synthflow at call time) | edit, commit, deploy (clears the cache) | Claude |
| 4 | Voice prompt `docs/synthflow-inbound-prompt.md` | edit, **paste into the Synthflow inbound agent by hand** | Kes |
| 5 | Synthflow agents (Cold, Warm/New, Inbound): live configuration | open each agent and confirm | Kes |
| 6 | **GHL Conversation AI agent "Cora" prompt** (AI Agents > Conversation AI > Agents List) | update `docs/ghl-conversation-ai-cora-prompt.md`, paste into GHL; confirm the right knowledge base is attached and the old one detached; test messages below | Kes |
| 7 | GHL knowledge bases (AI Agents > Knowledge Base) | update, archive the stale one | Kes |
| 8 | GHL Marketing > Snippets (texts and emails staff paste) | archive stale ones (watch for old-course payment reminders meant only for current students), update the rest | Kes |
| 9 | GHL workflows, SMS and email templates, campaign automations | search them for the old course and old facts | Kes |
| 10 | GHL appointment reminders and the reschedule workflow | read the wording | Kes |
| 11 | Eventbrite page, www.myfreeaiclass.com, training.colaberry.com | wording and link targets | Kes |
| 12 | Settings page: next class start, next Open House, sms delay etc. | confirm the dates are still true | Kes |
| 13 | Call-scoring prompt and date-expiry fallback text in the repo | grep for old wording | Claude |

## 3. Verify before calling it done
1. `python -m pytest tests/unit -q` passes (the offer and forbidden-terms tests protect Cora's own messages).
2. Deploy, then read the last 24 hours of what actually went out: Cora's texts and emails (`outbound_messages`) **and** the GHL assistant's texts. Zero retired-course terms, zero employment claims.
3. Send test messages to the GHL assistant: "Do I have to pay?", "Is there a scholarship?", "What is the employment rate?", "Do you teach SQL?", "STOP", and an automated business text. Confirm each reply matches the new facts or is silent.
4. Spot-check one voice call transcript from each Synthflow agent.
5. Watch the Delivery Health tile and the daily check for a week.

## 4. Record it
Add a dated entry to `PROGRESS.md`, update the register in `directives/spec/38_offer_single_source_roadmap.md` (status and date per row), and note anything new that was discovered as a place the offer lives.
