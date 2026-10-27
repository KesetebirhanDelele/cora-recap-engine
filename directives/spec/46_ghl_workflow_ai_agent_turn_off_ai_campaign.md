# 46 - GHL workflow "AI Agent - Turn off AI Campaign" (runbook, not code)

**Status:** documentation of a workflow that lives in GHL, not in this repo. Written 2026-10-27 so it can be rebuilt, audited or removed. Everything below was read from GHL screenshots and the GHL billing reports by Kes; Cora cannot read workflow triggers or steps through the API (workflows are only readable by name and status).

## What it is
| | |
|---|---|
| Name | `AI Agent - Turn off AI Campaign` |
| GHL workflow id | `53295fe7-d4ea-4b6a-a829-2b8eddcddfc2` (sub-account "Colaberry, School of Data & AI", location `ttBtJmxoLwjf18lIvvVD`) |
| Status | published; last edited 2025-07-18 (it predates Cora's finalization writes). **The two Google Sheets steps were disabled (not deleted) by Kes on 2026-10-27.** |
| Trigger | **Contact changed**, trigger name `AI Agent Turned off`; filter: field `AI Campaign` **Added** value `No` |
| Fires when | any contact's AI Campaign field becomes No, whoever wrote it (Cora, a staff member, another workflow) |

## Steps (as shown by Kes)
1. **Remove from Workflow** (mentioned by GHL's usage summary as the first step; the exact target workflow list was not captured - open the step in GHL to record it).
2. **Number Formatter #1 "Format Phone Number"** - normalises the contact phone for the lookup below.
3. **Google Sheets - Lookup Spreadsheet Row** (premium) - action name `Lookup Spreadsheet Row`; Google account `shveta@colaberry.com`; finds one row by matching a column to `Number Formatter.#1 Format Phone Number.Result`; "case sensitive lookup" and "create new row if it doesn't exist" options exist (their settings, and the drive / spreadsheet / worksheet / column, were not captured).
4. **Google Sheets - Update Specific Spreadsheet Row** (premium) - action name `Update Specific Spreadsheet Row`; same Google account; row number = `Google Sheets.#1 Lookup Spreadsheet Row.Row Number`; writes value **`No`** into column **`AI Campaign (B)`** (starting / ending column); spreadsheet and worksheet not captured.

Net effect: when AI Campaign is switched to No, the contact is taken out of the running automation and its row in a Google Sheet (matched by phone number) gets `AI Campaign = No`. The sheet is a tracking list of leads in the AI follow-up.

## What it cost, and why it spiked (2026-10-27 investigation)
- GHL bills only the premium steps, **$0.01 per execution each**: the two Google Sheets steps. The trigger, Remove from Workflow and Number Formatter are free. Billing shows them as `Workflow - Premium Features (google_sheets)`.
- GHL's own report: on **2026-10-26** the update step ran **2,280 times** (1,140 failed + 1,140 skipped), against 2 on 2026-10-25. The lookup step ran for the same contacts.
- Cause: Cora's bulk writes of `AI Campaign = No` that night (the opt-out and carrier cleanup batches, then the finalized-lead backfill of 2,577 leads, spec 43 / spec 45). Cora never calls this workflow; it only changes the field the trigger watches.
- The step was failing or skipping on most runs (the lookup found no matching row), so the sheet update was not doing useful work. Daily run count when nothing bulk is happening is small (2 on 10-25); the cost comes from bulk writes.
- The month's premium total was 5,943 executions ($59.43, more than double September); this workflow explains a large share of the increase but other workflows were not reviewed.

## Rules for the engine (Musts / Must-nots)
- **Must** treat every `AI Campaign = No` write as an event that runs this workflow once per contact (with the Google Sheets steps enabled: about $0.02 per contact; disabled: free).
- **Must not** run a bulk `AI Campaign = No` write (backfill, cleanup) without first telling Kes the contact count and cost.
- **Escalate** to Kes before any bulk write of a field that a published GHL workflow triggers on; spec 43 noted workflow triggers were never inspected.
- Nothing in Cora reads this Google Sheet or depends on this workflow; removing the two Google Sheets steps or the whole workflow cannot break Cora.

## To rebuild it (if it is deleted or re-created)
1. Automation > Workflows > Create > Start from scratch; name `AI Agent - Turn off AI Campaign`.
2. Trigger: **Contact changed**; name `AI Agent Turned off`; filter: `AI Campaign` / `Added` / `No`.
3. Add: Remove from Workflow (list the follow-up workflows that must stop); Number Formatter (format the contact phone as the sheet stores it); Google Sheets > Lookup spreadsheet row (match the phone column to the formatted number; account `shveta@colaberry.com`); Google Sheets > Update specific spreadsheet row (row number from the lookup; set column `AI Campaign (B)` to `No`).
4. Optional improvement (stops the wasted spend): add an If/Else after the lookup that continues only when a row number was found, or leave the two Google Sheets steps out entirely.
5. Publish and test with one contact: set its AI Campaign to No, then check the execution log shows each step ran once.

## Open items for Kes / Ali
- Capture, from the live workflow: the Remove from Workflow targets, the spreadsheet and worksheet names, the lookup column, and whether "create row if missing" is on.
- Review the other workflows keyed on `AI Campaign` (Cora Outbound - New Leads / Cold Leads / Cold Leads (II)); each bulk write to AI Campaign also triggers them.
