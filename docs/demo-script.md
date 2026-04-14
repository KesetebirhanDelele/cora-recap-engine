# Dashboard Demo Script
# Cora Voice AI — Monitoring & Operator Console

**Audience:** Executives + Technical Leads  
**Duration:** 25–35 minutes (full walk-through) or 15 minutes (exec-only, skip starred sections)  
**URL:** `http://<host>:3000`

---

## The story to tell

> "We replaced a fragile Zapier workflow with a production-grade voice AI pipeline.
> Every call, every decision, every failure is visible, auditable, and actionable in real time.
> This dashboard is the control room."

Keep that framing as the through-line. Each page answers one of three questions:
- **What is happening right now?** (Operations)
- **How are we performing?** (Analytics)
- **Is the system healthy?** (System)

---

## Opening — Home (/)

**What to point at:**

**Status bar (top strip):**
The bar has three signals:
- A **severity dot** — red "Critical" means at least one health metric is outside threshold (e.g. queue backlog). Green means all clear.
- A **SHADOW badge** (amber) — confirms GHL writes are intercepted and not yet hitting the live CRM. This is intentional during validation. "Shadow" is a safety mode, not an error.
- A **GHL: Shadow** label — same confirmation for the GHL integration specifically.

> **Important framing for execs:** If the status bar shows "Critical + Shadow" right now, that is expected. The "Critical" indicator reflects a queue backlog from test runs, not a live production failure. Shadow mode means nothing has reached GHL yet — the pipeline is fully validated before that switch is flipped.

**Three nav groups:**
- **Operations** (amber left column, two rows, 6 cards) — tools for the on-call operator: what's running, what's broken, what needs a human decision.
- **Analytics** (blue center, 4 cards) — business performance: pickup rates, intent, sales queue, campaign schedule.
- **System** (purple right column, 2 cards) — infrastructure health: CRM sync and anomaly detection.

**Card indicators:**
Each card shows a live metric badge computed from the database right now:
- `[0 events/min]` — Live Activity quiet (no calls in flight)
- `[0 open issues]` — Exceptions Monitor clean
- `[536 backlog]` — Queue Health showing accumulated test jobs (addressed at Stop 9)
- `[0 active alerts]` — Alerts clean
- `[3 calls/hr ↓]` — Contact Drill-Down shows call rate with trend arrow
- `[healthy config]` — Settings configured correctly
- `[5% pickup]` — Voice Performance pickup rate (low because test volume, not a real campaign)
- `[0% engagement]` — Engagement Analysis (no completed-call transcripts with intent yet)
- `[1 urgent lead]` — Sales Queue has one lead requiring attention
- `[3 leads]` — Campaign Overview has 3 scheduled actions upcoming
- `[100% sync]` — CRM Health showing perfect GHL task sync rate
- `[0 anomalies]` — System Anomalies clean

**What to say:**
> "This is the home screen. It loads in under a second and tells the operator what needs attention today — without opening a single subpage. The badge on every card is live data, not a cached snapshot. The three groups map to the three ways we run the system: operations for real-time response, analytics for business decisions, system for infrastructure health."

---

## Stop 1 — Live Activity (/activity)

**What to show:**
- The real-time event feed. Events stream in via WebSocket — each row shows event type, contact ID, and timestamp.
- Card shows `[0 events/min]` right now — explain this is because no calls are actively being processed at this moment.
- Point out the event types the feed surfaces: `job_completed`, `call_processed`, `exception_created`, `campaign_switched`.

**What to say (exec):**
> "This is the live pulse of the pipeline. Every call processed, every job completed, every exception surfaced in real time. During a campaign run you'd see one event every few seconds. Right now it's quiet — which is expected outside calling hours."

**What to say (tech):**
> "Events are published to a Redis pub/sub channel (`dashboard:events`). The dashboard API bridges that channel to a WebSocket. If Redis is down, the frontend automatically falls back to HTTP polling against an `event_stream` table in Postgres — no operator intervention required."

---

## Stop 2 — Sales Queue (/conversion-funnel)

**What to show:**
- The **priority ranking** column — 🔴 Urgent, 🟡 Review, ⚪ None. The home card showed `[1 urgent lead]` — find that row here.
- Point out **Lead name, phone, sales score (0–100), and Recommended Action** — all computed by the system, not manually assigned.
- **Expand a row** to show the transcript preview and the AI-analyzed intent surfaced underneath.
- **Outcome form** — show the dropdown: Booked, Follow Up, Not Interested, No Answer, Voicemail, Wrong Number. Logging an outcome here writes to the database and removes terminal leads from the queue.
- **Filter bar** — filter by voice agent (New Lead / Cold Lead / Inbound) or by date range.
- **CSV export button** — one click, downloads the current filtered and sorted view.

**What to say (exec):**
> "This is where sales reps start every morning. The system has already ranked every lead by urgency. Red means call now — this person showed buying intent and hasn't been reached in over 15 minutes. The rep doesn't decide who to call next. The system decides. When the call is done, they log the outcome right here and the lead drops off the queue."

**What to say (tech):**
> "Priority score is computed server-side on every fetch: intent maps to a base score 0–100, then a recency bonus is added for leads not contacted recently. Urgent is ≥ 80, Review is ≥ 40. Nothing is cached — the ranking reflects the current state of the database at query time."

---

## Stop 3 — Campaign Overview (/campaign-overview)

**What to show:**
- The home card showed `[3 leads]` — those appear here.
- The **date window selector** — show upcoming actions for the next 7 days.
- The table: scheduled calls, SMS, and email follow-ups by contact, job type, and scheduled time.

**What to say (exec):**
> "This is the forward view — every automated touchpoint scheduled for the coming week. A manager can see how many leads are at which tier and when the next contact attempt is for each one. No spreadsheet, no manual tracking."

**What to say (tech):**
> "This is a live query against `scheduled_jobs WHERE status = 'pending' AND run_at` falls within the selected window. Nothing is pre-computed. The date filter is applied server-side."

---

## Stop 4 — Voice Performance (/voice-performance)

**What to show:**
- The **KPI sidebar** — pickup rate (home card showed `[5% pickup]`), blank transcript rate, average call duration, total calls. Explain the 5% is from test volume, not a live campaign.
- The **Trends chart** — calls over time. Change the date range to show how the view adapts.
- The **Week-over-Week waterfall** — which metrics improved or declined versus the prior period.
- The **Efficiency scatter** — call duration vs outcome. Outliers indicate calls that ran long without a result.

**What to say (exec):**
> "These are the business KPIs. Pickup rate is the headline signal — if it drops, we look at calling window configuration, lead list quality, or the AI greeting. The week-over-week view tells us immediately if we're trending the right direction without needing a separate report. The 5% you see here reflects test calls, not a real campaign — in a live campaign this would run 30–50%."

**What to say (tech):**
> "All metrics are computed from `call_events`. The WoW calculation compares the selected window against the same-length window immediately prior — no separate aggregation table, pure SQL window functions."

---

## Stop 5 — Engagement Analysis (/engagement-analysis)

**What to show:**
- The home card showed `[0% engagement]` — explain this is because no completed-call transcripts with detectable intent have been processed yet.
- The **AI Quality tiles** — blank transcript rate, consent rate, average confidence. In a live campaign these would be populated.
- The **Intent Distribution bar chart** — what leads are saying. Click a bar to drill into the specific calls behind that intent.
- The **Consent Distribution chart** — YES / NO / NOT_DETECTED breakdown.
- The **Intent → Outcome table** — which intents actually convert to bookings.
- The **filter bar** — filter by campaign, voice agent, call direction.

**What to say (exec):**
> "This is the AI's report card. Consent rate tells us what percentage of leads want a follow-up. The intent chart tells us what's in leads' minds — too many 'not interested' in a campaign signals a list quality problem. The intent-to-outcome table shows which signals convert to bookings so we can optimize the script around what works."

**What to say (tech):**
> "Intent classification runs GPT-4o mini on every completed call transcript. The priority order is deterministic — `do_not_call` always wins over `not_interested` over `enrolled`, and so on. The same transcript always produces the same routing outcome. You can click any bar in the intent chart to see the individual calls behind that intent category."

---

## Stop 6 — Contact Drill-Down (/contact-lookup) ⭐ (technical depth)

**What to show:**
- The home card showed `[3 calls/hr ↓]` — the down arrow means call rate is declining vs the prior period.
- Type in a phone number or contact ID from a known test call. Hit search.
- Walk through **Lead State** — current status, campaign, tier, last updated.
- Walk through **Call History** — every call with status, duration, and detected intent.
- Walk through the **Pipeline Trace** — the full chronological job timeline: every job that ran, when it started, when it completed, how long it took, whether it failed. Shadow actions appear with a badge.
- If an exception is linked to a step, show it inline.

**What to say (exec):**
> "If a lead or a rep calls asking 'why didn't I get a call back?' — we have the complete answer in 10 seconds. Every automated touchpoint, every AI decision, every exception, in order. No digging through logs."

**What to say (tech):**
> "The pipeline trace joins `scheduled_jobs`, `shadow_actions`, and `exceptions` by `contact_id` ordered by `created_at`. Failed steps show the exception ID inline — one click jumps to the exception record. Shadow actions have a visual badge — these are GHL writes that were intercepted because `GHL_WRITE_MODE=shadow`. In live mode the same trace appears, but the shadow badges become real write confirmations."

---

## Stop 7 — Exceptions Monitor (/exceptions)

**What to show:**
- Home card showed `[0 open issues]` — clean right now. Explain this is the ideal state.
- Walk through the UI anyway: the exception list, severity, type, and created timestamp.
- Describe the **action buttons**: Retry Now, Retry in N minutes, Cancel Future Jobs, Force Finalize, Ignore.
- Explain what Retry does: creates a new `scheduled_jobs` row, moves the exception to resolved, writes an audit log entry.

**What to say (exec):**
> "Zero open exceptions means the pipeline is healthy — every job completed or failed cleanly with no outstanding decisions for a human. When something does fail — a GHL API timeout, an OpenAI failure, a bad lead state — it surfaces here instead of silently retrying forever. The operator clicks Retry, the system re-runs it, and the audit trail records who took that action."

**What to say (tech):**
> "Every write action requires a Dashboard Token (Bearer auth). Retry is idempotent — the system checks for an existing pending job before creating a new one, so a double-click can't create duplicates. The API returns HTTP 409 on a conflict. All actions write to `audit_log` with entity type, entity ID, action, and operator ID."

---

## Stop 8 — Alerts (/alerts)

**What to show:**
- Home card showed `[0 active alerts]` — clean. The status bar said "Critical" earlier, but alerts are clear — explain the distinction: the Critical dot reflects the queue backlog metric directly; the Alerts page only shows threshold-triggered email alerts.
- The **Active tab** and **Acknowledged tab**.
- Describe the 5 alert types: queue lag exceeded, error rate spike, exception spike, worker offline, GHL auth failure.
- Walk through what an Acknowledge action does: moves the alert off Active, doesn't suppress future ones.

**What to say (exec):**
> "The system sends an email when a threshold is breached — queue lag over 5 minutes, error rate over 20%, workers going offline. You get one email per incident, not one per minute. When the metric recovers, an automatic 'all clear' email is sent. Operators acknowledge here to confirm they've seen it — it's a signal, not a dismissal."

**What to say (tech):**
> "Alert deduplication is stored in `alert_events` with a configurable window (default 1 hour). The metrics collector self-schedules every 60 seconds via a worker job. All thresholds — lag seconds, error rate, exception count — are environment variables. No code deploy needed to tune them."

---

## Stop 9 — Queue Health (/queue) ⭐ (technical depth)

**What to show:**
- Home card showed `[536 backlog]` — this is the most prominent number on the home screen. Address it directly.
- Open Queue Health. The **Stuck Jobs table** shows jobs that have been pending more than 10 minutes past their `run_at`.
- Explain: these are accumulated test and development jobs. They are not real leads waiting for outreach — they are leftover scheduled_jobs rows from earlier testing cycles.
- Show the **Cancel jobs button** on rows with a contact ID — explain that in a production incident, this is how an operator stops all pending outreach for a specific lead with one click.
- Show the **Expired Leases table** — jobs claimed by a worker whose lease has since expired. Point out the note: auto-recovered by the worker, no manual action needed.

**What to say (exec):**
> "This 536 backlog is from our development and testing work — jobs queued during pipeline validation that were never run because the worker wasn't active during those periods. In production, this counter should stay near zero because the worker processes jobs as they're scheduled. The Queue Health page is where an operator comes when something is stuck — one click cancels all pending outreach for a contact."

**What to say (tech):**
> "Job claiming uses optimistic locking — `UPDATE WHERE version = expected`. If two workers try to claim the same job simultaneously, exactly one succeeds. Expired leases are auto-recovered by `recover_expired_claims` on every worker cycle — the job goes back to pending and a new worker claims it. The two tables on this page show the two failure modes separately: stuck-pending (no worker claimed it) vs expired-lease (worker crashed mid-job)."

---

## Stop 10 — CRM Health (/crm-health)

**What to show:**
- Home card showed `[100% sync]` — GHL task creation and VM message update success rates are both at 100%.
- Shadow write activity — how many GHL writes were intercepted vs sent live.
- Point out that 100% sync in shadow mode means the pipeline is generating correct outputs; they just haven't been sent to GHL yet.

**What to say (exec):**
> "100% sync means every GHL task we attempted to create succeeded — no dropped records, no auth failures. Because we're in shadow mode, those writes were logged rather than sent to GHL. When we flip to live mode, this same counter measures real CRM write reliability."

**What to say (tech):**
> "Shadow mode intercepts writes at the adapter layer in `app/adapters/ghl.py`. The shadow dict is logged to `shadow_actions` with a human-readable field map. CRM Health aggregates these alongside live writes so the sync rate metric is meaningful in both modes."

---

## Stop 11 — System Anomalies (/system-anomalies)

**What to show:**
- Home card showed `[0 anomalies]` — nothing detected.
- Describe what this page surfaces when it is populated: exception volume spikes, call volume spikes, recurring failure patterns, cluster analysis.

**What to say:**
> "Zero anomalies is the right answer here. This page is not for individual records — it's for structural patterns. A spike in `send_sms_failed` every night at 2am is a pattern. Individual exceptions are noise. This page finds the signal before it becomes an incident. When something does appear here, it's a proactive warning, not a reactive alert."

---

## Closing — Settings (/settings)

**What to show:**
- Home card showed `[healthy config]` — all required settings are present and valid.
- **Calling windows** — allowed hours and days. An operator can change this without a code deploy.
- **Tier delays** — wait times between voicemail attempts, configurable here.
- **Dashboard Token card** — where operators paste the `SECRET_KEY` value to enable write actions across the dashboard.

**What to say:**
> "Operators tune system behavior here — calling windows, follow-up delays — without touching code or triggering a deploy. The Dashboard Token card is the one-time setup step that enables all write actions: retry, cancel, acknowledge. Once set, it persists in the browser until cleared."

---

## Closing statement

> "Everything you just saw runs on a single server. The pipeline is fully auditable — every decision, every AI output, every operator action is logged and queryable. Shadow mode means GHL is protected while we validate. The 536-job backlog on Queue Health represents our testing history, not live failures. When we flip to production mode, the same code, the same jobs, the same dashboard carries over — with real GHL writes replacing the shadow logs."

---

## Demo preparation checklist

Before the demo, verify:

- [ ] Dashboard is reachable at `http://<host>:3000`
- [ ] You can explain the "Critical" status bar dot (queue backlog from testing, not a live failure)
- [ ] You can explain the "SHADOW / GHL: Shadow" badges (intentional safety mode)
- [ ] At least one contact exists in Contact Drill-Down with a full pipeline trace
- [ ] Dashboard Token is set in browser localStorage (`/settings` → Dashboard Token card → paste `SECRET_KEY`)
- [ ] If demoing exception actions: confirm a test exception exists and the Dashboard Token is valid
- [ ] Sales Queue shows at least 1 lead (home card currently shows `[1 urgent lead]`)

---

## Addressing the current dashboard state during the demo

The live dashboard shows indicators that need proactive framing — address them early rather than waiting for questions:

| What they'll see | What to say |
|---|---|
| 🔴 Critical in the status bar | "That reflects the queue backlog from our test runs, not a live pipeline failure. I'll show you what that means at Queue Health." |
| SHADOW / GHL: Shadow | "Shadow mode is intentional — GHL writes are intercepted and logged so we can validate the full pipeline before touching the live CRM." |
| [536 backlog] on Queue Health | "Those are leftover test jobs. In production this stays near zero because the worker processes jobs as they're scheduled." |
| [5% pickup] on Voice Performance | "That's test call volume, not a live campaign. A real campaign runs 30–50% pickup during peak hours." |
| [0% engagement] on Engagement Analysis | "No completed-call transcripts with detectable intent yet from the test data set." |

---

## Fallback talking points if data is sparse

| Page | Fallback |
|---|---|
| Sales Queue | "In a live campaign this list shows every lead from the past 7 days ranked by urgency. Today it reflects our test volume — that one urgent lead is a real record." |
| Live Activity | "Quiet right now because no calls are actively being processed. During a campaign run you'd see one event every few seconds." |
| Engagement Analysis | "The 0% engagement reflects no completed-call intent data yet. Once real call transcripts flow through, every bar in this chart populates automatically." |
| Exceptions Monitor | "Empty is the ideal state — zero open issues means every job either completed or was handled cleanly." |

---

## Exec-only run order (15 minutes)

Skip: Queue Health (deep dive), System Anomalies, Contact Drill-Down (pipeline trace section), CRM Health.

**Order:** Home → Live Activity → Sales Queue → Campaign Overview → Voice Performance → Engagement Analysis → Exceptions Monitor → Alerts → Settings → Closing statement.

When passing Queue Health on the home screen, briefly note: "The 536 backlog is from test runs — I'll come back to that if there's time, but the key point is this page is where operators intervene when something is genuinely stuck."
