Talk track for Customer Support staff taking calls from current students/leads for the
**AI Systems Architect Accelerator**. Unlike the sales script, this isn't a fixed pitch — it's a
method for handling whatever the caller actually needs, well.

> **Engineering note:** these calls are recorded via GHL's native dialer, transcribed, and scored
> against `_SUPPORT_RUBRIC_PROMPT` in [`app/core/call_quality_scoring.py`](../app/core/call_quality_scoring.py)
> (see [`directives/spec/23_staff_call_quality_analysis.md`](../directives/spec/23_staff_call_quality_analysis.md)).
> The rubric scores you against the caller's *actual* request, identified from the transcript —
> not against a fixed checklist. This script is written to make that go well.

---

## 1. Acknowledge the actual issue *(scored: Acknowledgment)*

Before responding, make sure you can state back — in your own head, if not out loud — what the
caller is actually calling about. Don't respond to what a GHL ticket field says the issue is, or
what a previous call was about; the person in front of you may be calling about something else
entirely, or something has changed since the ticket was logged.

- Let them finish explaining before you start solving.
- If it's ambiguous, ask a clarifying question rather than guessing: "Just to make sure I've got this right — you're asking about X, or Y?"
- Repeat the issue back in your own words before moving to a fix. This isn't just good practice — it's literally the first thing the call gets scored on.

**Fails this section if:** you respond to a script or a stale ticket note instead of what the caller actually said.

---

## 2. Give accurate information *(scored: Accuracy — weighted heavily)*

Wrong information causes real downstream harm (a missed deadline, a payment mistake, a student
losing access) — this dimension is weighted more heavily than the others for that reason.

- If you're not 100% sure of a policy, deadline, or pricing detail, **check before you answer** — don't guess and don't extrapolate from a similar-sounding case.
- Current program pricing (if it comes up): Free preview / $149 mo Annual / $199 mo Month-to-Month, plus a separate ~$20/mo Claude Code + ~$10/mo API tooling cost not included in the Colaberry price.
- Payment/IPBC questions from **current Data Analytics bootcamp students** route to Taiwo (she/her) — don't attempt to answer these yourself if you're not the payments owner.
- If you don't know, say "let me confirm that and follow up" rather than answering with unverified confidence. An honest "I'll check" scores better than a wrong answer delivered confidently.

**Fails this section if:** you give an answer you weren't sure of instead of verifying or escalating.

---

## 3. Resolve it, or own the next step *(scored: Resolution)*

- If you can resolve it on this call, do — don't punt something you're equipped to handle.
- If you can't resolve it on this call, don't leave it at "someone will get back to you." Give a **specific, owned** next step: who, roughly when, and what happens if that doesn't happen.
- Confirm with the caller before ending the call that they know what happens next.

**Fails this section if:** the call ends with the issue unresolved and no concrete next step, or a vague promise nobody is accountable for.

---

## 4. Know when to escalate *(scored: Escalation Judgment)*

Escalating appropriately is scored *well* — it is not a sign of failure. Guessing on something
outside your scope, or stalling instead of escalating, is what gets penalized.

**Staff routing (use correct pronouns — never guess from a name):**
- **Admissions:** Roselen (she/her) — Mon–Fri, 9AM–5PM CST
- **Payments / IPBC** (current DA bootcamp students only): Taiwo (she/her) — Mon–Fri, 9AM–5PM CST
- **Customer Support:** Balakrishna (he/him) — Mon–Fri, 4:30AM–12:30PM CST
- **Customer Support:** Farhat (she/her) — Mon–Fri, 12:00PM–8:00PM CST
- **Customer Support:** Balamurali (he/him) — Mon–Fri, 6:00PM–2:00AM CST; Sat, 9:00AM–1:00AM CST
- **WhatsApp / Community:** Jackie (she/her) — jackie@colaberry.com

**If the right person is unavailable, in this order:**
1. Offer a scheduled callback
2. Take name + contact info and promise a specific follow-up
3. Offer support@colaberry.com as backup

**Fails this section if:** you attempt to handle something outside your scope instead of routing it, or you escalate something you were actually equipped to resolve yourself (under-escalating and over-escalating both cost points).

---

## 5. Tone *(scored: Tone)*

- Patience and empathy — especially with a frustrated caller. A frustrated caller is reacting to a real problem, not attacking you personally.
- Don't get defensive or dismissive, even if the caller is short with you or the issue isn't your fault.
- Slow down with a frustrated caller rather than rushing to close the call — being heard is often part of the resolution.

**Fails this section if:** you respond defensively, dismissively, or rush a caller who's clearly still upset.

---

## Using CSAT / ticket context correctly

GHL ticket fields and any CSAT survey answers on file are **supporting context only** — they may
be stale or about a different issue than what's raised on this call. Use them to inform your
answer, never to override what the caller is telling you live on the call.

---

## What gets a call flagged

Straight from the rubric — avoid these outright:
- Incorrect information given to the caller
- The caller left the call with no real next step
- Clear misconduct (dismissiveness, refusal to escalate something outside your scope, etc.)

If you realize mid-call (or after) that you gave wrong information, don't let it ride — flag it to
your lead so it can be corrected with the caller. A corrected mistake is a much smaller problem
than an uncorrected one.
