# Dashboard Feature Use Cases

This document describes what every section of the Cora Dashboard v2 is for, who uses it, and what question it answers. It is written for an operator or engineer who is unfamiliar with the system and needs to understand which page to open and why.

---

## Overview: What the dashboard is and is not

The dashboard is a **read-only monitoring and operator-action console** for the Cora Voice AI pipeline. It shows what the automated system has done, what it is doing right now, and what is failing. It does not configure the pipeline, does not replace manual CRM work, and does not control Synthflow or GHL directly.

There are two personas:

- **Operations team** — uses the home page, Exceptions Monitor, Queue Health, Live Activity, and Alerts to keep the system running cleanly.
- **Analyst / manager** — uses Voice Performance, AI Performance, Conversion Funnel, and System Anomalies to understand how campaigns are performing.

---

## Home Page (`/`)

**Question answered:** Is the system healthy right now? Where do I go?

The home page is the single entry point. It has two parts:

### Status Bar (top strip)

The status bar synthesizes the entire system into one signal: **Healthy / Warning / Critical / Unknown**. It derives this from:
- Queue lag (>60s = warning, >300s = critical)
- Open exception count (>0 = warning, ≥10 = critical)
- Error rate (>5% = warning, >20% = critical)
- Stuck jobs or expired leases (>0 = warning)

Below the status dot, it shows:
- When the health snapshot was last taken
- Whether the system is in **Shadow** mode (writes are logged but not sent to GHL/Synthflow) or **Live** mode
- The GHL write mode (`shadow`, `live`, etc.)
- A live count of active alerts, linking to the Alerts page

If there are active alerts, each one appears as a colored row beneath the strip. These are the same records on the Alerts page — the strip surfaces them so an operator never has to navigate just to discover there is a problem.

### Navigation Cards

Three groups of cards, each linking to a detail page:
- **Operations** (amber): Live Activity, Exceptions Monitor, Queue Health, Alerts
- **Analytics** (blue): Voice Performance, AI Performance, Conversion Funnel
- **System** (purple): CRM Health, System Anomalies

Operations cards show live badge counts (e.g. the Exceptions Monitor card shows how many open exceptions exist; the Live Activity card shows how many jobs completed in the last 5 minutes). This lets an operator see at a glance whether there is something to action without clicking anything.

---

## Live Activity (`/activity`)

**Question answered:** What is the pipeline doing right now?

Shows a real-time stream of job events emitted by the worker: calls processed, AI analysis completed, callbacks scheduled, campaign switches, and so on. Events arrive via WebSocket (`/dashboard/ws/events`) with a polling fallback (`/dashboard/events?since=<cursor>`).

**Use cases:**
- Watch a test call flow through the system end-to-end (call received → AI analysis → GHL update → callback scheduled)
- Confirm that jobs are being processed after a worker restart
- Spot a specific contact's event in real time after a Synthflow webhook fires
- Verify that a shadow action was logged instead of executing a live write

---

## Exceptions Monitor (`/exceptions`)

**Question answered:** What is broken right now and what do I need to do about it?

This is the primary operator action page. It shows individual exception records that have been raised by the worker and not yet resolved. Each exception represents a failure that paused or ended a contact's processing flow.

### Metrics summary bar
Three counters at the top:
- **Today** — exceptions created since midnight UTC
- **Open** — exceptions in `open` status (requiring action)
- **Resolved (24h)** — exceptions resolved in the last 24 hours (shows whether the queue is being worked)

### Exceptions Trend chart
A stacked bar chart showing daily exception counts grouped by category (Call / AI / CRM / System) over a configurable date range. Use this to see whether today is an outlier or whether a problem has been building over days.

### Filters
- **Status tabs** — Open / Resolved / Ignored. Open is the default action view. Resolved shows what has been handled. Ignored shows what was deliberately dismissed.
- **Severity** — All / Critical / Warning
- **Type** — Dropdown of every known exception type (e.g. `openai_error`, `ghl_update_failed`, `call_processing_failed`)
- **Date range** — Optional; checkbox-gated so daily use is not cluttered

### Grouped exception list
Exceptions are grouped by type. Each group card shows:
- Severity chip (critical = red, warning = blue)
- Category chip (Call / AI / CRM / System) — color-coded to match the trend chart
- Exception type (human-readable)
- Occurrence count badge if more than one

Clicking a group expands it to show individual records, each with:
- Entity ID (usually a contact_id)
- Time ago
- Context fields from the exception payload (up to 4 key-value pairs)
- **Resolve** and **Ignore** action buttons for open exceptions

For groups with many occurrences, a **Bulk Ignore** button dismisses all in one click.

**Use cases:**
- Every morning: open the Exceptions Monitor and check the Open tab. Resolve anything that has a recovery path, ignore noise, escalate anything that indicates a code or integration bug.
- After a GHL outage: switch to type filter `ghl_update_failed`, bulk-ignore or retry the backlog.
- After a batch of Synthflow calls completes: check whether any `call_processing_failed` exceptions appeared.

---

## System Anomalies (`/system-anomalies`)

**Question answered:** Is there a pattern in the errors? Is this a new problem or a recurring one?

This page is the intelligence layer above the Exceptions Monitor. It does not show individual records — it shows patterns derived from the same exception data over longer windows.

### Spike Detection
Identifies exception types where the last-24-hour count is higher than the 7-day daily average. For each spike it shows:
- Category and type
- Count in the last 24 hours
- Baseline daily average from the prior 7 days
- Spike multiplier (e.g. ×4.2)
- "New type" badge if this exception type has never appeared before

**Use case:** An operator who checks the system daily will see at a glance whether today's exception volume is an anomaly or normal noise.

### Anomaly Frequency (trend line)
A 14-day line chart of total daily exception count. Rising trends indicate a worsening problem. Flat or declining trends indicate the system is stable or recovering.

### Recurring Issues
A table showing every exception type that has appeared in the last 30 days, sorted by total count. Columns: type, category, severity, total count, open count, resolved count, last seen time.

**Use case:** Identify systemic problems that are not acute spikes but are quietly accumulating. A type with `total: 47 / open: 31 / resolved: 16` means the team is not keeping up with a recurring failure mode.

### Failure Clusters
Contacts or entities that have accumulated 2 or more failures in the last 7 days. Each cluster shows:
- Entity ID (contact_id or job entity)
- Entity type
- All exception types the entity has hit
- Total failure count (red background if ≥5)
- Time of last failure

**Use case:** Find specific contacts whose processing is stuck in a loop. These are candidates for manual investigation or cancellation via the lead pipeline trace.

---

## Queue Health (`/queue`)

**Question answered:** Are there jobs stuck in the pipeline that will never run?

Shows two lists:
- **Stuck jobs** — jobs whose `run_at` time has passed but that have not been claimed by a worker. Fields: job_id, job_type, contact_id, run_at, lag in seconds.
- **Expired leases** — jobs that were claimed by a worker but whose lease expired before completion (worker crashed or timed out). Fields: job_id, job_type, worker_id, lease age in seconds.

The home page badge on this card shows `stuck_job_count + expired_lease_count`.

**Use cases:**
- After a worker restart: check whether any leases are in an expired state and whether they have been auto-recovered.
- When queue lag is elevated (visible on the status bar): open Queue Health to see which specific jobs are causing the lag.
- Periodic audit: confirm no jobs are silently stuck behind a dead worker.

---

## Alerts (`/alerts`)

**Question answered:** What thresholds has the system crossed, and have I acknowledged them?

Alerts are generated automatically by the alerting service when metrics exceed configured thresholds. Five alert types exist:

| Type | Trigger |
|---|---|
| `queue_lag_exceeded` | Queue lag exceeds `ALERT_QUEUE_LAG_THRESHOLD_SECONDS` (default 300s) |
| `error_rate_spike` | Error rate exceeds `ALERT_ERROR_RATE_THRESHOLD` (default 20%) |
| `exception_spike` | Open exception count exceeds `ALERT_EXCEPTION_COUNT_THRESHOLD` (default 10) |
| `worker_offline` | No active workers detected |
| `ghl_auth_failure` | GHL authentication failing |

Each alert record has a severity (critical / warning), a status (active / resolved / acknowledged), the current metric value, the threshold it crossed, and the alert message. An email is sent when an alert is first triggered.

The page has three tabs: **Active**, **Resolved**, **Acknowledged**.

**Use cases:**
- **Active tab** — the queue to action. Critical alerts (red) require immediate attention. Warning alerts (amber) should be investigated.
- **Resolved tab** — audit trail; confirms alerts self-resolved after the underlying metric recovered.
- **Acknowledged tab** — alerts that have been noted but not yet resolved. Useful when a known incident is being worked.

---

## Voice Performance (`/voice-performance`)

**Question answered:** How are the voice AI campaigns performing across all call metrics, week over week?

This is the primary analytics page for the voice channel. It is a single-screen dashboard (no scroll) with four panels.

### KPI Sidebar (left column)
Nine performance KPI cards that each show:
- Metric name
- Current period value (large number)
- Week-over-week change badge (▲ green or ▼ red)

KPIs included:
- Unique Contacts, Booked Appointments, Calls Per Day
- Call Completion Rate, Call Duration (seconds)
- Pickup Rate, Voicemail Rate, Failed Rate, Booking Rate

Rate KPIs are color-coded to match the corresponding line in the Trends chart. This makes it immediately obvious which trend line corresponds to which KPI tile.

### Trends Over Time (main chart)
A composed chart with a date-selectable range (default: last 28 days):
- **Stacked bars** (left Y axis) — weekly total calls split by campaign: Cold Lead (blue `#2563eb`), New Lead (green `#16a34a`), Inbound (yellow `#eab308`)
- **Lines** (right Y axis) — Completion %, Pickup %, Voicemail %, Failed %, Booking Rate %, each in its own color

X axis shows ISO week ranges formatted as `MM/DD–MM/DD`.

Hovering any point shows a tooltip:
- Hovering a **bar segment** (e.g. the Cold Lead bar) recalculates all KPIs — rates, unique contacts, booked appts, avg duration — for that specific campaign in that week. The tooltip header says "— Cold Lead only" to make this clear.
- Hovering a **line** shows a focused card for that single metric with its value and a context line (e.g. total unique contacts that week).

### WoW % Performance (bottom left)
A waterfall bar chart showing the week-over-week percentage change for each metric. Green bars = improvement, red bars = decline. Each bar has a ▲/▼ label. The tooltip shows the exact value and whether there is no prior-period data.

**Use case:** Every Monday review. See at a glance which metrics moved and in which direction. Drill into any declining metric.

### Are We Wasting Calls? / Efficiency Scatter (bottom right)
A bubble chart with:
- X axis: Pickup Rate
- Y axis: Booking Rate (Booked Appointment %)
- Bubble size: Total Calls volume

One bubble per campaign (exactly three: Cold Lead `#2563eb`, New Lead `#16a34a`, Inbound `#eab308`). A campaign that is large (many calls), low on pickup rate, and low on booking rate is wasting dial capacity.

**Use case:** Prioritize which campaign to investigate for efficiency improvements. A bubble in the top-right is performing well. A large bubble in the bottom-left is a problem.

---

## AI Performance (`/ai-performance`)

**Question answered:** Is the AI correctly understanding calls, and what are leads saying?

Covers AI-specific quality metrics only — no business KPIs.

### AI Quality tiles
- **Calls Analyzed** — total call events in the period
- **Blank Transcript Rate** — percentage of calls with no usable audio/transcript (amber warning >10%)
- **Unknown Intent %** — percentage classified as `low_confidence_audio` (amber warning >15%)
- **Intents Classified** — total calls where AI returned a valid intent

### Intent Distribution (horizontal bar chart)
All detected intents sorted by frequency, each in its canonical color. Use this to understand what leads are saying across all campaigns.

Key intents and their business meanings:
| Intent | Meaning |
|---|---|
| `enrolled` | Lead booked an appointment |
| `re_engaged` | Previously cold lead shows renewed interest |
| `callback_request` / `callback_with_time` | Lead wants a call back |
| `interested_not_now` | Soft positive — nurture candidate |
| `not_interested` | Disqualified for this cycle |
| `do_not_call` | Remove from all outreach |
| `low_confidence_audio` | Audio too poor to classify |

### Consent Distribution (vertical bar chart)
Shows how often the AI detected each consent outcome (YES / NO / PARTIAL / UNKNOWN). The `allows_writeback` flag is true only when consent is `YES` — this controls whether GHL notes and custom fields are updated.

**Use case:** If the consent YES rate is low, it may indicate a script change is needed or that the AI is being overly conservative.

### Intent → Outcome Mapping table
Every intent with its call count, share of total calls, and booking rate (100% for `enrolled`, 0% for all others — this is correct: only enrolled calls generate an appointment in this system).

### AI Error & Quality Trends (line chart)
Weekly trend of Blank Transcript Rate and Unknown Intent % over the selected date range. Rising values indicate audio quality degradation or a model quality problem.

---

## Conversion Funnel (`/conversion-funnel`)

**Question answered:** At which stage are we losing leads, and is it getting better or worse?

### Drop-off alert banner
Automatically identifies the funnel stage with the highest percentage loss and displays it as a red alert at the top. E.g. "Biggest drop-off at Engaged: 68.3% of previous-stage leads lost here."

### Funnel Overview (visual bars)
Four stages: **Total Calls → Picked Up → Engaged → Booked**

Each stage is a proportional horizontal bar. The bar width is relative to stage 1 (Total Calls = 100%). Inside each bar, the step conversion rate is printed.

Stage derivations:
- **Total Calls** — from `kpis.total_calls`
- **Picked Up** — `total_calls × pickup_rate`
- **Engaged** — sum of all intent distribution counts where the intent indicates genuine engagement (enrolled, re_engaged, callback_request, callback_with_time, interested_not_now, partial_engagement, human_transfer_request, failed_booking, call_later_no_time, request_sms, request_email)
- **Booked** — `kpis.enrolled_count` (only `enrolled` intent = booked appointment)

### Step Breakdown table
Shows count, % of total calls, step conversion rate, and drop-off % for each stage. Drop-off is the percentage of the previous stage that did not make it to the current one.

### Conversion Rates Over Time (line chart)
Weekly Pickup Rate and Booking Rate trend lines. Use this to confirm whether changes to the call script or campaign targeting are improving or degrading funnel performance over time.

---

## CRM Health (`/crm-health`)

**Question answered:** Are GHL writes succeeding, and how much activity has been in shadow mode?

Three metrics:
- **GHL Task Success Rate** — percentage of task creation/update attempts that succeeded. Amber warning if below 90%.
- **GHL VM Update Success Rate** — percentage of voicemail-related GHL field updates that succeeded. Amber warning if below 90%.
- **Shadow Write Count (period)** — number of GHL writes intercepted by shadow mode during the selected period. This should be 0 when the system is in live mode; non-zero values in live mode indicate a configuration issue.

**Use case:** When transitioning from shadow mode to live mode, monitor this page for the first several days to confirm GHL write success rates are acceptable and no shadow writes are leaking.

---

## Lead Pipeline Trace (`/lead/[id]`)

**Question answered:** What happened to this specific lead, in what order, and did anything fail?

A per-contact timeline view. Accessed by entering a contact_id (not linked from the home page; navigate directly or via a future exception detail link).

Shows a chronological list of pipeline steps for the contact:
- Job type (e.g. `process_call_event`, `run_call_analysis`, `schedule_callback`)
- Status (pending / claimed / running / completed / failed / cancelled)
- Start and completion timestamps
- Duration in milliseconds
- Whether the step was a shadow action
- If failed: failure reason and linked exception_id
- Payload summary (key fields from the job input)

**Use case:** When an operator receives a complaint that a specific lead was not processed correctly, or when an exception record references a contact_id, the trace provides a complete audit trail of every job that ran for that contact.

---

## Campaign Overview (`/campaign-overview`)

**Question answered:** Who has a scheduled action coming up, and what campaign are they in?

Shows all leads with an upcoming scheduled action (call, SMS, email) within a configurable date window. Terminal leads (Do Not Call, Invalid, Enrolled, Closed) are always included regardless of the date window.

### Controls
- **Next action from / to** — date range picker (default: today → today + 7 days). Filters non-terminal leads to those whose scheduled action falls within the window.
- **Campaign** — dropdown filter: All / New Lead / Cold Lead / Unknown. `Unknown` shows leads whose campaign cannot be resolved from either `lead_state.campaign_name` or `lead_state.lead_stage`.
- **Refresh** button — manual reload.

### Campaign resolution logic
Each row's campaign is resolved in priority order:
1. `lead_state.campaign_name` — if it contains "Cold Lead" or "New Lead" (case-insensitive)
2. `lead_state.lead_stage` — same check
3. `call_events.voice_agent` from the most recent call — `ColdLead` → "Cold Lead", `NewLead` → "New Lead"
4. Fallback: "Unknown"

This ensures that leads whose `campaign_name` is null or empty are still classified correctly based on which Synthflow agent last called them.

### Table columns
- **Phone** — clickable; opens the Contact Lookup drill-down for that contact
- **Campaign** — resolved campaign name (Cold Lead / New Lead / Unknown)
- **Last Call (CST)** — timestamp of the most recent call event, in Central Time
- **Next Action** — label and timing (e.g. "Call in 2h 15m", "Follow-up in 1d 4h", "Unscheduled")
- **Status** — terminal status badge if applicable (Do Not Call / Invalid / Enrolled / Closed)

**Use case:** Morning queue review. An ops rep opens Campaign Overview to see which leads need to be called today and in what campaign. Clicking a phone number opens the full contact detail without leaving the page.

---

## Contact Lookup (`/contact-lookup`)

**Question answered:** What is the complete current state of this specific contact?

Accepts a contact ID or phone number and returns:
- Lead state (campaign, status, VM tier, DNC flag, next scheduled action)
- Full call history (all call events with status, duration, direction, voice agent)
- Scheduled jobs (pending and recent)
- Shadow actions (intercepted writes, if any)

Also used as the drill-down view within Campaign Overview: clicking a phone number in the Campaign Overview table loads the Contact Lookup view inline, with a "Back to Campaign Overview" button to return.

**Use case:** An operator receives a complaint about a specific lead. Enter the contact_id or phone number to see exactly what the pipeline has done to that lead, what is scheduled next, and whether anything failed.

---

## Engagement Analysis (`/engagement-analysis`)

**Question answered:** How do campaign performance metrics change when I slice by campaign type, call direction, or Synthflow voice agent?

A cross-filter analytics view over the same KPI and AI distribution dataset used by the Voice Performance page, with additional filter dimensions.

### Filters
- **Date range** — from / to date pickers
- **Campaign** — `New Lead` | `Cold Lead` | `Unknown` | (all). "Unknown" matches leads where neither `campaign_name` nor `lead_stage` resolves to a standard campaign.
- **Call Direction** — `Inbound` | `Outbound` | (all). **Outbound is defined as NOT Inbound**: it matches `call_events` rows where `direction IS NULL OR LOWER(direction) != 'inbound'`. This covers NULL values and any non-inbound variant, not just an exact string match on "outbound".
- **Voice Agent** — `ColdLead` | `NewLead` | `Inbound` | (all). Matches the Synthflow agent identifier stored in `call_events.voice_agent`. This is distinct from campaign: a single campaign can be handled by different voice agents over time.
- **All filters apply to every metric including the consent distribution chart.** The consent query joins `summary_results` to `call_events` so that the active campaign, direction, and voice agent filters constrain the consent counts.

### Panels
- **KPI tiles** — same metrics as Voice Performance (total calls, pickup rate, voicemail rate, etc.), recomputed for the active filter combination
- **Intent distribution** — bar chart of detected intents for the filtered call set
- **Consent distribution** — YES / NO / UNKNOWN bars filtered by the full active filter set
- **Intent → Outcome table** — intent frequency and booking rate for the filtered set
- **AI trend lines** — blank transcript rate and unknown intent % over time

**Use case:** An analyst wants to know whether the Inbound voice agent has a different booking rate than the ColdLead agent. Set Voice Agent to "Inbound", observe KPIs; switch to "ColdLead", compare. Or: filter to "Unknown" campaign to audit leads that are not being classified correctly.

---

## Settings (`/settings`)

**Question answered:** What are the current runtime configuration values, and can I change them without redeploying?

Displays the active `app_config` values loaded from the database. These are runtime settings (brand name, messaging templates, alert thresholds, tier delays) that can be updated without a code deploy.

Write actions require `Authorization: Bearer {SECRET_KEY}`. The settings page uses the same auth flow as the operator action endpoints.

**Use case:** Update the voicemail SMS message template or adjust an alert threshold during a live incident without restarting any service.

---

## System Status Bar (home page component)

**Question answered:** Is this a safe time to make changes to the system?

The status bar is the first thing visible on the home page. Its color and label answer immediately:

- **Green / Healthy** — all metrics within normal range; safe to deploy or make configuration changes
- **Amber / Warning** — at least one metric is elevated; investigate before making changes
- **Red / Critical** — immediate operator action required; do not make changes until resolved
- **Gray / Unknown** — the health endpoint returned an error; health data is stale; treat as warning

The **Shadow** vs **Live** pill is critical context. All dashboards and reports will reflect real call data in both modes, but in Shadow mode no writes reach GHL or Synthflow. This pill prevents operators from being confused about whether the system is actually processing leads or just logging what it would do.

---

## API Endpoints Reference (Dashboard API v2, port 8001)

All read endpoints are optionally auth-gated by `DASHBOARD_READ_AUTH_REQUIRED`. All write/action endpoints require `Authorization: Bearer {SECRET_KEY}`.

| Method | Path | Used by |
|---|---|---|
| `GET` | `/dashboard/health` | Home page, status bar |
| `GET` | `/dashboard/metrics` | Engagement Analysis, AI Performance, Conversion Funnel, CRM Health, Queue Health — accepts `campaign`, `direction`, `voice_agent`, `from_date`, `to_date` |
| `GET` | `/dashboard/card-metrics` | Home page navigation card badge counts |
| `GET` | `/dashboard/events` | Live Activity (polling fallback) |
| `WS` | `/dashboard/ws/events` | Live Activity (WebSocket primary) |
| `GET` | `/dashboard/lead/{id}/trace` | Lead Pipeline Trace |
| `GET` | `/dashboard/lead/{id}/detail` | Contact Lookup — full contact detail view |
| `GET` | `/dashboard/alerts` | Alerts page, SystemStatusBar |
| `GET` | `/dashboard/exceptions` | Exceptions Monitor |
| `GET` | `/dashboard/exceptions/trend` | Exceptions Monitor (trend chart) |
| `GET` | `/dashboard/exceptions/anomalies` | System Anomalies |
| `GET` | `/dashboard/voice-performance` | Voice Performance, Conversion Funnel |
| `GET` | `/dashboard/ai-timeseries` | AI Performance |
| `GET` | `/dashboard/campaign-overview` | Campaign Overview |
| `GET` | `/dashboard/recent-calls` | Contact Lookup, Campaign Overview |
| `GET` | `/dashboard/intent-calls` | Engagement Analysis intent drill-down |
| `GET` | `/dashboard/settings` | Settings page (read) |
| `POST` | `/dashboard/settings` | Settings page (write, auth required) |
| `POST` | `/dashboard/actions/retry` | Exceptions Monitor → Retry |
| `POST` | `/dashboard/actions/cancel` | Lead trace / operator action |
| `POST` | `/dashboard/actions/finalize` | Lead trace / operator action |
| `POST` | `/dashboard/actions/resolve` | Exceptions Monitor → Resolve |
| `POST` | `/dashboard/actions/ignore` | Exceptions Monitor → Ignore |
| `POST` | `/dashboard/actions/bulk-ignore` | Exceptions Monitor → Bulk Ignore |

---

## Color System

The dashboard uses a consistent color system across all pages. Understanding it makes charts self-documenting.

### Campaign colors
| Campaign | Color | Hex |
|---|---|---|
| Cold Lead | Blue | `#2563eb` |
| New Lead | Green | `#16a34a` |
| Inbound | Yellow | `#eab308` |

### Rate line colors (Voice Performance, KPI sidebar)
| Metric | Color | Hex |
|---|---|---|
| Completion Rate | Sky blue | `#0ea5e9` |
| Pickup Rate | Emerald | `#10b981` |
| Voicemail Rate | Violet | `#8b5cf6` |
| Failed Rate | Red | `#ef4444` |
| Booking Rate | Purple | `#7c3aed` |

### Status / severity colors
| State | Color | Hex |
|---|---|---|
| Critical / Error | Red | `#dc2626` |
| Warning | Amber | `#d97706` |
| Success / Healthy | Green | `#16a34a` |
| Neutral / Muted | Slate | `#64748b` |

### Exception category colors
| Category | Color | Hex |
|---|---|---|
| Call exceptions | Blue | `#2563eb` |
| AI exceptions | Purple | `#7c3aed` |
| CRM exceptions | Teal | `#0891b2` |
| System exceptions | Slate | `#64748b` |

---

## Frequently Asked Questions

**Q: I see "Shadow" in the status bar. Are calls being made?**
A: In Shadow mode, the pipeline processes calls and runs AI analysis, but outbound actions (new calls, GHL writes, SMS) are intercepted and logged to `shadow_actions` instead of being executed. Shadow mode is used during initial deployment and testing to validate behavior without side effects.

**Q: The Exceptions Monitor shows 0 open exceptions but the status bar is amber. Why?**
A: The status bar considers multiple signals. It may be amber due to stuck jobs, expired leases, failed jobs in the last 5 minutes, or a non-zero error rate — even if the exception queue itself is clean. Check Queue Health for stuck/expired jobs and the Alerts page for threshold breaches.

**Q: Voice Performance shows 4 bubbles in the efficiency scatter chart.**
A: This should not happen. The scatter chart renders exactly three canonical campaigns: Cold Lead (blue `#2563eb`), New Lead (green `#16a34a`), and Inbound (yellow `#eab308`). If four bubbles appear, the campaign names in the database contain a variant not handled by the normalizer — investigate the raw `campaign_name` values in the `call_events` table.

**Q: Why does the Engagement Analysis "Outbound" direction filter show all calls when I expect only outbound ones?**
A: "Outbound" is defined as NOT Inbound. The filter matches `call_events` rows where `direction IS NULL OR LOWER(direction) != 'inbound'`. This is intentional: most calls use the default direction value of `"outbound"` (lowercase), and some may be NULL. An exact match on `"Outbound"` (capital-O) would miss nearly everything. If you expect a specific direction value in the data, check the raw `direction` column in `call_events`.

**Q: Campaign Overview shows "Unknown" for some contacts even though I know their campaign.**
A: The Campaign Overview resolves campaign from three sources in order: (1) `lead_state.campaign_name`, (2) `lead_state.lead_stage`, (3) the `voice_agent` field of the most recent call event (`ColdLead` → Cold Lead, `NewLead` → New Lead). If all three are absent or non-standard, the contact shows as "Unknown". Check the contact's `lead_state` record and most recent `call_events.voice_agent` to diagnose why resolution is failing.

**Q: The WoW badges on the KPI sidebar show "—" for everything.**
A: "—" means no prior-period data exists for comparison. This appears the first time a date range is selected (no previous week to compare against) or if the prior week had zero calls.

**Q: Conversion Funnel shows "Engaged" as higher than "Picked Up". Is that a bug?**
A: The page caps Engaged at the Picked Up count (`Math.min(engagedCount, pickedUp)`) to prevent this. If it still appears higher, the underlying data has an inconsistency (AI classified more calls as engaged than the pickup rate implies picked up). This is a data quality signal worth investigating.

**Q: Where do I find a specific lead's history?**
A: Navigate directly to `/lead/{contact_id}` (replace with the actual GHL contact_id). This is not linked from the home page. Future versions may add a search box.
