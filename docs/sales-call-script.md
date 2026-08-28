Talk track for Admissions/sales staff taking or making live calls for the **AI Systems Architect
Accelerator**. Follow the flow below in order — the sections are labeled with the rubric
dimension they satisfy so you can see exactly what a good call sounds like and why.

> **Engineering note:** these calls are recorded via GHL's native dialer, transcribed, and scored
> against `_SALES_RUBRIC_PROMPT` in [`app/core/call_quality_scoring.py`](../app/core/call_quality_scoring.py)
> (see [`directives/spec/23_staff_call_quality_analysis.md`](../directives/spec/23_staff_call_quality_analysis.md)).
> This script is written to score well against that rubric — it is not a separate, competing set
> of rules. If the rubric changes, this script should change with it.
>
> This is a **talk track, not a word-for-word script**. The rubric penalizes reciting a generic
> pitch instead of listening — use your own words, hit the substance.

---

## 1. Opening & Rapport *(scored: Opening/Rapport)*

- Identify yourself and Colaberry clearly in the first sentence — no dead air, no guessing who's calling.
- Confirm you're speaking with the right person before diving in.
- Ask how they're doing, actually listen to the answer, and give it a beat before pivoting to business. A warm 15 seconds here beats jumping straight to the pitch.
- If they sound rushed or guarded, acknowledge it ("I know I'm catching you out of nowhere — this'll be quick") rather than pushing through it.

**Fails this section if:** you launch straight into the program pitch with no greeting/rapport beat, or you don't confirm identity first.

---

## 2. Discovery *(scored: Discovery)*

Before you pitch anything, find out:
- What made them look into the program in the first place (form fill, referral, ad, past conversation)?
- What's their current situation — working, between roles, career-switching?
- What do they actually want out of this — a job change, a raise, a specific skill (AI/agents), a portfolio?

Use what they tell you to shape everything that follows. Don't recite the same pitch to a working engineer exploring AI tooling and a career-changer with no technical background — the *facts* below don't change, but which ones you lead with should.

**Fails this section if:** you pitch before asking anything, or the questions are surface-level ("are you interested?") rather than actually shaping the rest of the call.

---

## 3. Value Articulation *(scored: Value Articulation)*

Frame the program against what they just told you, using these facts (keep them accurate — see Compliance below):

- **Program:** AI Systems Architect Accelerator — 12-week online course for working professionals who want to design, build, and lead AI-powered systems (not just use AI tools).
- **Curriculum arc:**
  - Weeks 1–3 — AI Foundation: Claude Code, Claude API, core agent skills
  - Weeks 4–6 — Build Your AI Team: prompt engineering, MCP, multi-tool agent design
  - Weeks 7–9 — Connect AI to the Real World: multi-agent systems, real workflow automation, reliability
  - Weeks 10–12 — Design AI That Scales: governance frameworks (NIST AI RMF, ISO 42001, EU AI Act), full system architecture, live Expo presentation
- **Format:** Two live sessions/week (Mon Architecture Day, Thu Build Day), 2 hrs each, all recorded within 24 hrs, fully online CST, community access via portal + WhatsApp.
- **Outcomes:** Anthropic Architect Certification (CCA-F exam prep included), a GitHub portfolio of real projects, access to a full-time internship track.
- **Enrollment:** Rolling, no fixed start or capped cohort — free preview anytime; full Classroom access unlocks when their cohort starts.

Lead with the 1–2 facts that map to what they said they wanted in Discovery, not the full list every time.

**Fails this section if:** you deliver a generic, unpersonalized pitch regardless of what discovery surfaced.

---

## 4. Objection Handling *(scored: Objection Handling)*

Acknowledge the concern before you answer it — don't talk over it or brush past it.

Common objections and how to work them:
- **Cost:** Walk through the free Explorer preview as a no-payment way to start, then the paid tiers (see Compliance for exact numbers). Don't minimize the concern — validate it, then show the lowest-friction path.
- **Time:** 4 hours/week live + recordings available within 24 hrs if they miss a session. Rolling enrollment means no pressure around a fixed start date.
- **Confidence / "not technical enough":** The program is built for professionals moving *beyond* using AI tools, not for people who already architect systems — reassure without overstating what week-1 looks like.
- **"I need to think about it":** Don't push past this. Confirm what specifically they want to think through, and offer the free preview as a zero-commitment next step while they decide.

If no objections come up, that's fine — you don't need to manufacture one.

**Fails this section if:** an objection is acknowledged then ignored, steamrolled, or answered with pressure instead of information.

---

## 5. Compliance — non-negotiable *(scored: Compliance)*

These are hard rules, not style preferences. A violation here flags the call regardless of how the rest went.

- **No guaranteed job placement, ever.** Say "prepare for opportunities" or "position yourself for roles in AI" — never "you will get a job."
- **If pricing comes up, disclose both, accurately:**
  - Colaberry program: Free ($0 preview) / Annual ($149/mo) / Month-to-Month ($199/mo)
  - Separate Anthropic tooling cost: ~$20/mo Claude Code + ~$10/mo API — this is **not** included in the Colaberry price and must be mentioned alongside it, not omitted.
- **SMS or appointment offers require explicit permission first.** Ask "would it be OK if I text you a summary?" / "can I go ahead and book that for you?" before doing either.
- **Scholarships:** none are currently available. Say so directly — don't imply one might materialize. If cost is the blocker, offer to connect them with Admissions instead of overpromising.
- **Never offer or imply enrollment in the Data Analytics bootcamp** — it's closed to new students. Current DA bootcamp students with payment/IPBC questions route to Taiwo (she/her), not through this script.
- **Identify yourself and Colaberry at the start of the call** (covered in §1, but it's also a compliance requirement, not just rapport).
- **Pronouns when referring to staff in the third person:** Roselen, Taiwo, Farhat, Jackie — she/her. Balakrishna, Balamurali — he/him. Never guess from a name.

---

## 6. Confirm the Next Step *(scored: `next_step_confirmed`)*

Every call should end with a concrete outcome, not a vague "I'll follow up." Examples of a confirmed next step:
- They agree to start the free Explorer preview today
- They agree to enroll and you walk them through it now
- A specific follow-up call is booked (with their permission) at a specific time
- They explicitly decline further contact — that's still a confirmed outcome, record it

"They said they'd think about it and I said I'd check back" is **not** a confirmed next step unless you locked a specific date/channel to check back on.

---

## What gets a call flagged

Straight from the rubric — avoid these outright:
- Any compliance violation from §5
- A misleading claim about the program, pricing, or outcomes
- Pressure tactics (rushing a decision, minimizing a real objection, implying scarcity that isn't real)

If something goes wrong on a call — a fact you got wrong, a promise you shouldn't have made — say so to your lead rather than hoping it doesn't get flagged. A self-reported miss is a coaching conversation; a flagged call nobody mentioned is a bigger one.
