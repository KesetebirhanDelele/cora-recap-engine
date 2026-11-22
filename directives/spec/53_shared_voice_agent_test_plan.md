# Spec 53 - Shared outbound voice agent: test plan (steps 2 and 3)

Status: plan written 2026-11-21. Ali confirmed the proposal on 2026-11-20 (5:14 PM) and approved steps 2 and 3. Nothing is built yet. This is work in GHL, not in the Cora code: it is recorded here so it has one home and a measurable "done".

Sources: Kes's proposal email of 2026-11-20 with attachment `shared-voice-agent-proposal.md` (sections 4, 7, 8, 11) and Ali's reply of 2026-11-20. The attachment is not in this repo. Anything below marked UNVERIFIED was not proven in the proposal either.

## 1. Self-contained problem statement
**Business goal.** Ali wants one general-purpose outbound voice agent that any of his AI agents (for example Maya for admissions) can use as a tool, for "Enterprise application" calls. The person to call and the instructions are supplied at run time. Cora's AI campaigns are not involved.

**How it works (proposal).**
1. Ali's app finds the person's GHL contact.
2. Ali's app checks the person may be contacted: Do-Not-Disturb, opt-out tags, its own opt-out list, and the call limits. GHL does not check these.
3. Ali's app writes four contact fields in one update: `call_purpose`, `call_organization`, `call_script`, `call_facts`. `call_facts` is written last and must differ on every attempt, because that change is what starts the GHL workflow.
4. The workflow runs one new Voice AI agent and the call goes out.
5. Ali's app reads the call back from GHL (duration, transcript, summary) and records the outcome: confirmed, a stop request, or something for a person to follow up.

**The agent.** No fixed name or purpose; it says only that it is an AI assistant calling on behalf of `call_organization` about `call_purpose`. Locked in its prompt (a script can add steps, never override these): AI disclosure, identity check, wrong-person handling, stop-calling, voicemail wording, no personal-data requests, no employment-outcome claims, and "a team member will reach out" for anything not in the facts. Its confirmation step asks: "Can you confirm you got this information? Please just say yes."

**In scope now:** Kes builds in GHL the four contact fields, the new agent with its prompt, a workflow that triggers on `call_facts` and is pinned to ONE test contact, a stop-request action that adds the do-not-call tag, and then makes ONE real test call.
**Out of scope now:** the shared service, moving the interview-notification calls to the new agent, the callback section for the receptionist (proposal section 8), and anything that calls a real person other than the test contact.

## 2. Plan, in order
| # | Step | Owner | Depends on | Done when |
|---|---|---|---|---|
| 1 | Create the four contact fields in GHL | Kes | none | all four exist, with the exact names above |
| 2 | Create the new Voice AI agent with the locked prompt, using the four fields as merge fields | Kes | 1 | agent saved; prompt keeps the confirmation question wording above |
| 3 | Create the workflow: trigger = `call_facts` changes; action = run the new agent; stop-request action adds the do-not-call tag; **pinned to one test contact** | Kes | 2 | workflow published; a different contact cannot trigger it |
| 4 | Make ONE real test call between 8 AM and 8 PM in the test contact's time zone | Kes | 3 | the checklist in section 3 is filled in |
| 5 | Report the result to Ali, with transcript lines for each checklist item | Kes | 4 | Ali has the result (his side writes nothing until it arrives) |
| 6 | Only after 5: Ali's side builds the shared service; the interview-notification calls move to the new agent | Ali | 5 | out of scope here; the existing agent stays unchanged until then |

## 3. Acceptance criteria: the test call checklist
Pass means every line is observed in the transcript or call log, not assumed.
1. The AI disclosure is spoken at the start.
2. The identity question is asked.
3. The facts are stated.
4. The unique reference at the end of `call_facts` is NOT read aloud.
5. A plain "yes" to the confirmation question is captured as a candidate line in the log.
6. A stop request adds the do-not-call tag.
7. **Wrong person (added by Ali):** the agent shares NOTHING from the facts before it ends the call.
8. **Voicemail (added by Ali):** the agent leaves only the short locked message, with no details.
9. All four merge fields were actually filled in the call (UNVERIFIED: only first name and the current interview field are proven).

Items 7 and 8 and the stop request need their own test calls or scripted answers; one happy-path call does not cover them. If a call cannot be arranged for 7 or 8, record it as "not tested", not as passed.

## 4. Constraints
**Musts**
- Call only 8 AM to 8 PM in the contact's time zone.
- Respect GHL's limits: one call per phone number per day, 14 in 14 days, 10 calls a minute, 5,000 a day per location, domestic numbers only.
- Pin the workflow to the test contact until Ali has the result.
- Keep the confirmation question wording unless Ali is told first (his app finds the answer by that exact wording; reference in Kes's other app: `CONFIRM_QUESTION_MARKER` in `execution/ghl_conversations.py`).

**Must-nots**
- No call to a real person other than the test contact.
- No change to the existing interview-notification agent.
- Do not paste the webhook secret or any credential into this file or the plan's emails.

**Escalation triggers (stop and tell Ali)**
- A merge field is not filled in the call.
- The agent reveals any fact to a wrong person, or leaves details on a voicemail.
- A stop request does not add the do-not-call tag.
- The workflow fires for any contact other than the test one.

## 5. Ali's side (from his reply)
- Nothing on his side writes a field until the test result is in.
- His agents run under an approval rule: anything composed for a real person waits for a person to approve it. His writer will have an approval hold, so an approved script plus a clean consent check produces a PENDING call, not a call. The field write therefore lands later than the proposal implied. It also means two of his apps will not race for one contact on one day.
- The safety switches the proposal cited (GHL_WRITE_MODE and two others) exist only in Kes's interview-notification app, not in Ali's. Where proposal section 11 says "the app already does this", that is Kes's app; Ali builds all of it from scratch. Section 11 should be corrected.

## 6. Open items
1. **Content scan.** Calls are not scanned today; texts and emails are. Ali asks whether the scan in the shared service is his to build or Kes's. Suggested answer (Kes to decide): his, since he owns the service; Kes supplies the text and email rules as the starting point.
2. **Confirmation wording.** Ali asks to be told if it changes. It does not: the new agent keeps "Can you confirm you got this information? Please just say yes."
3. Whether the per-call prompt can be overridden through GHL's API is UNVERIFIED and not needed for the field approach.
4. How a real callback or stop request reads in the call log is UNVERIFIED until a call is made.
5. Email sending limits are not documented; none are stated. Confirm before any volume.
6. A test contact must be chosen (suggested: Kes's own phone, to be confirmed) and a time inside the 8 AM to 8 PM window.

## 7. Rollback
Unpublish the workflow. The existing interview-notification agent was never touched, so nothing else needs to be restored.

## 8. Evidence to keep
For each checklist line: the transcript line or log entry, the call time and the test contact id (not the phone number). Attach to the report to Ali.
