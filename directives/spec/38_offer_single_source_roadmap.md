# 38 - Offer single source of truth: remediation roadmap

**Status:** PROPOSED 2026-10-02. Nothing in phases 2-6 is built. Phase 0 (inventory) is done; phase 1 needs decisions from Kes.
**Document type:** remediation roadmap + source-of-truth register (section 3) + course-change runbook (phase 5). Builds on spec/37.

## 1. Problem
Colaberry's only current course is the **AI Systems Architect Accelerator**. The offer (name, facts, price, links, dates) is
written by hand in many places that nothing keeps in sync. When the course changed, the voice prompts and knowledge base were
updated and the follow-up generator was not, so in the 14 days to Oct 1, 44% of generated SMS drafts and 61% of generated email
drafts still named the retired Data Analytics course (Power BI 14%, SQL 4% of emails). The code path was fixed on Oct 1 (spec/37).
The structural cause is still open: there is no single source, no check that copies agree, and no procedure for a course change.

**Out of scope:** rewriting voice prompts (confirmed correct by Kes); Synthflow and GHL SMS workflow prompt review (excluded by Kes
on 2026-10-01 - they appear below only as places to verify, not to rewrite); support for current Data Analytics students
(payments / IPBC), which must keep working.

## 2. Goal and acceptance criteria
1. One structured offer record is the only place the code reads course name, facts, price, links and retired terms from.
2. A change to the course in that record, or in the voice prompts / knowledge base, cannot reach production while any copy disagrees: an automated test fails first.
3. Outgoing text and email are audited after sending; a retired-course term or a message missing the course name raises an alert within one hour.
4. Every place outside the repo that carries offer wording is listed, has an owner, and has been checked at least once after each course change.
5. The rule is written into CLAUDE.md, spec/37 and a runbook, so a future session or intern follows it without being told.

## 3. Source-of-truth register (where offer wording lives today)
Status key: OK = matches the voice prompts; GAP = disagrees or unverified; ? = not yet inspected.

| # | Location | Kind | Owner | Status |
|---|---|---|---|---|
| 1 | `docs/synthflow-cold-lead-prompt.md`, `synthflow-warm-lead-prompt.md` | Repo, loaded into Synthflow at call time | Kes | OK (declared the reference) |
| 2 | `docs/synthflow-inbound-prompt.md` | Repo, pasted into Synthflow by hand | Kes | OK |
| 3 | `docs/colaberry-knowledge-base.md` | Repo, no code reads it; hand-synced to GHL | Kes | OK |
| 4 | `app/core/offer.py` (+ `app_config` overrides `offer_name`, `offer_facts`, `offer_forbidden_terms`) | Repo, feeds follow-ups | Eng | OK; omits prices and the voice "ready to enroll" link (see D1, D3) |
| 5 | Follow-up prompts `vm_sms_notice`, `vm_followup_generator` | Repo | Eng | OK since Oct 1 |
| 6 | Older generator `vm_content_generator` (`app/services/ai.py`) | Repo | Eng | GAP: "do not fabricate" with no facts; still in use is unconfirmed |
| 7 | Success-story library `app/prompts/knowledge_base/video_transcripts.csv` | Repo | Eng | GAP: 228 transcripts of the retired program; no code loads it now |
| 8 | `date_expiry.py` fallback wording, `call_quality_scoring.py` prompt | Repo | Eng | OK today, but each is a separate copy |
| 9 | App settings: next class start (shows Nov 12), next Open House (Oct 29) | `app_config` | Kes | GAP: Nov 12 unconfirmed against "rolling enrollment" |
| 10 | GHL Marketing > Snippets (screenshot 2026-10-02): "1st Payment Reminder" (text, "Hey Future Data Analyst!") and "1st Payment Reminder Email" (Data Analytics), both Apr 06; "50% scholarship lead" (text, Apr 06); AI snippets updated Sep 21-28 | GHL, used by staff by hand | Kes | GAP: the first two may be legitimate for current Data Analytics students; the scholarship text is lead-facing and unread. List was cut off at the bottom of the screenshot. |
| 11 | GHL AI Agents > Knowledge Base: "Colaberry Knowledge Base" (updated Oct 1 6:18 PM, **29 KB gaps**) and "Existing knowledge base" (last updated Feb 19, 2026, 0 gaps) | GHL | Kes | GAP: the Feb 2026 base predates the new course; which agents read it is unknown. 29 gaps = questions the agent could not answer. |
| 12 | Synthflow agents (Cold, Warm/New, Inbound): live configuration in Synthflow, not the repo files | Synthflow | Kes | ? Kes reports updated; no automated confirmation |
| 13 | GHL email templates, workflows, SMS templates, campaign automations | GHL | Kes | ? Out of scope for rewriting; must be listed and checked once |
| 14 | Eventbrite Open House page, myfreeaiclass.com, training.colaberry.com | External | Kes | ? Link targets and wording not checked |

## 4. Decisions needed (Phase 1 blockers)
| ID | Question | Why it matters |
|---|---|---|
| D1 | Should follow-up emails state prices ($149 a month annual, $199 month-to-month), as voice and the knowledge base do? | Call and email must not give different answers |
| D2 | Voice prompts say rolling enrollment and never mention the Open House or the self-paced versus live split. Add that to voice, or drop it from email? | A caller and a follow-up currently describe two different paths |
| D3 | Should a lead who says "ready to enroll" get the training.colaberry.com link in follow-ups (voice does)? Texts allow only the free link today. | Matches voice behaviour |
| D4 | Is Nov 12 a real class start? If not, clear the setting. | A wrong date is what the wrong-date monitor exists to catch |
| D5 | Which GHL snippets and which knowledge base are in active use, and which can be archived? | Defines the cleanup in phase 4 |

## 5. Phases (each independently verifiable)
| Phase | Purpose | Work | Done when | Depends on |
|---|---|---|---|---|
| 0 | Inventory | Section 3 register | Done 2026-10-02 (rows 10-14 need Kes's review of GHL) | none |
| 1 | Decisions | Kes answers D1-D5 | Answers recorded in this file | none |
| 2 | Single record + guard | Structured offer file (name, facts, prices, links, retired terms, legacy-allowed contexts). Test compares it against rows 1-3 (name, duration, hours a week, prices, links) and scans every prompt and template in the repo for retired terms outside the allowed contexts (the "closed to new students" and current-student support lines). Remove or quarantine the story library (row 7). | Test passes on today's files; a deliberately edited price or term makes it fail | 1 |
| 3 | Wire the consumers | Rows 4, 5, 6, 8 read the one record; settings keep only per-deploy overrides; fix or retire the older generator | No course name, price or link is written in any prompt string | 2 |
| 4 | Clean up GHL | Kes reviews rows 10-11 and 13: archive or rewrite stale snippets, retire the Feb 2026 knowledge base if unused, work through the 29 knowledge-base gaps. Engineering first checks whether the GHL API can read snippets and the knowledge base; if yes, add them to the phase-2 test, if not they stay on the manual checklist. | Each row marked OK or archived, with the date | 1 |
| 5 | Monitor | Hourly post-send audit of the last 24 h of outgoing messages: retired term, or no course name where one is required, raises an alert. Dashboard tile shows the last audit. Weekly Kes-run checklist for rows 12-14. | Seeded bad message triggers the alert in a test; tile live | 2 |
| 6 | Govern | CLAUDE.md "Offer Consistency Rule", a Definition-of-Done line, a consumer registry, `directives/runbooks/course-change.md` (every place in section 3, in order), an ADR recording the single-source decision, a PROGRESS entry, a memory note | Documents merged; the runbook was used for a dry run | 2-5 |

## 6. Constraints
**Musts:** the record is the only place the code reads offer facts; current Data Analytics students keep payment and IPBC support; checks run without sending anything.
**Must-nots:** no course name, price, date or link hand-written in a prompt or template; no writes to GHL or Synthflow from the automated test; no rewriting of voice prompts or of Synthflow / GHL SMS workflow prompts.
**Preferences:** fail closed (a message that fails the check falls back to the fixed notice, as today).
**Escalate to Kes when:** the voice prompts and the record disagree and it is unclear which is right; any GHL change is needed; a retired term appears in a sent message.

## 7. Evaluation design
1. Happy path: today's repo files pass the consistency test.
2. Negative: change the price in the offer file only, then separately a term in a prompt; each fails with a message naming the file.
3. Allowed context: the "closed to new students" bootcamp lines in the voice prompts do not trip the term scan.
4. Audit: a seeded message containing "Power BI" raises the alert; a clean message does not.
5. Regression: spec/37 tests (`tests/unit/test_sms_notice.py`) still pass.
