# spec/13_reporting_analytics.md

## Purpose
Define the requirements for the Cora Voice AI Agent Performance dashboard, including KPI scorecards, trends over time, drill-down behavior by date, and KPI tooltips.

## Reporting goals
The reporting layer shall allow business users to measure campaign and call-center performance across inbound, cold-lead, and new-lead workflows.

The dashboard must support:
- high-level KPI monitoring
- time-series trend analysis
- drill-down by date range and period
- per-call-type performance comparison
- tooltip inspection of KPI values for a selected date bucket
- identification of wasted-call patterns via pickup-rate vs booked-appointment performance

## Primary dashboard views
### 1. KPI scorecard rail
The left rail shall display the following KPIs for the selected date range:
- Unique Contacts
- Booked Appts
- Calls Per Day
- Call Completion Rate
- Call Duration in Sec.
- Pickup Rate
- Voicemail Rate
- Failed Rate

For each KPI the dashboard shall display:
- current value
- WoW% or comparison change value
- directional indicator (up/down/flat)

### 2. Trends Over Time chart
The Trends Over Time visual shall support:
- stacked bar display of call volumes by call type: `Cold`, `Inbound`, `New`
- overlay line series for percentage metrics such as:
  - Call Completion %
  - Pickup %
  - VM %
  - Failed %
  - Booked Appt %
  - Booked Appts Rate
- date-bucket grouping by week by default
- drill-down by date bucket to finer time grains when enabled by policy
- tooltip display for selected KPI values for the hovered date bucket

### 3. WoW% Performance chart
The WoW% chart shall display period-over-period changes for selected KPIs, including increase, decrease, and total contribution.

### 4. Are We Wasting Calls? bubble chart
The bubble chart shall compare call types using:
- X axis: Pickup Rate
- Y axis: Booked Appt %
- bubble size: configurable volume metric, default call count
- bubble grouping by call type
- drill-down by date bucket when date filters or drill actions are applied

## Filters and interaction model
### Global filters
The dashboard shall support at minimum:
- date range filter
- call type filter
- campaign filter where applicable
- direction filter where applicable

### Cross-filtering
Selecting a supported visual element shall cross-filter the related visuals unless explicitly disabled by dashboard configuration.

### Out of scope for current phase
- drill-down by date
- KPI tooltip interactions

Minimum behavior:
- selecting a date bucket filters related visuals to the same date bucket
- drill-down shall support at least one finer time grain below the default view, such as week -> day
- tooltip values must reflect the active filtered/drilled context
- clearing the drill or selection restores the parent aggregation level

### Cross-filtering
Selecting a call type or date bucket in one visual shall cross-filter the other visuals unless the dashboard config explicitly disables that interaction.

## Tooltip specification
When a user hovers over a Trends Over Time bucket, the tooltip shall display KPI values for that exact filtered date bucket.

Minimum tooltip fields:
- Unique Contacts Reached
- Booked Appts
- Calls Per Day
- Call Completion Rate
- Call Duration
- Pickup Rate
- Voicemail Rate
- Failed Call Rate

Tooltip values shall be computed from the same authoritative reporting dataset and must match the selected date-bucket context.

## Metric definitions
### Unique Contacts
Count of distinct contacts with at least one qualifying call event in the selected time window.

### Booked Appts
Count of booked appointments attributed to qualifying calls in the selected time window.

### Calls Per Day
Total qualifying calls in the selected period divided by the number of days in the selected date window.

### Call Completion Rate
Completed calls divided by total qualifying calls.

### Call Duration in Sec.
Average call duration in seconds across qualifying completed calls.

### Pickup Rate
Calls answered by a human or otherwise qualifying as picked up divided by total qualifying calls.

### Voicemail Rate
Calls ending in voicemail or voicemail-hangup outcomes divided by total qualifying calls.

### Failed Rate
Calls with failed or unsuccessful technical/completion outcomes divided by total qualifying calls.

### Booked Appt %
Booked appointments divided by total qualifying calls.

### Booked Appts Rate
Booked appointments divided by unique contacts reached, unless business definition changes by explicit rule.

## Data sources
The reporting layer shall read from the authoritative application database, not directly from Google Sheets.

Allowed sources:
- Postgres reporting views or tables derived from authoritative operational tables
- optional Power BI semantic model built on Postgres

Google Sheets may be used only for reconciliation during shadow mode and shall not be the authoritative reporting source in steady state.

## Reporting data requirements
The system shall persist or derive the following dimensions:
- date
- week start / week bucket
- call type (`Inbound`, `Cold`, `New`)
- campaign name
- call direction
- contact ID
- call ID
- lead ID
- call outcome
- duration
- appointment outcome
- pickup outcome
- voicemail outcome
- failed outcome

The system shall persist or derive the following facts:
- total calls
- completed calls
- picked-up calls
- voicemail calls
- failed calls
- booked appointments
- unique contacts reached
- average duration

## Performance requirements
- Default dashboard load shall complete within 5 seconds for the standard date range in the BI layer.
- Tooltip display shall render within 1 second after hover for cached or pre-aggregated views.
- Drill-down interaction shall complete within 3 seconds for the standard date range.

## Acceptance criteria
1. Given a date range is selected, when the dashboard loads, then all KPI cards reflect that exact range.
2. Given a user applies a call type or campaign filter, when filters propagate, then all supported visuals recalculate for that filtered scope.
3. Given a user selects a supported visual element, when cross-filtering is enabled, then related visuals update to the same context.
4. Given the dashboard uses Postgres-derived reporting tables, when Google Sheets shadow mode is disabled, then dashboard metrics remain available and unchanged in behavior.
5. Drill-down and KPI tooltip behaviors are not required in the current phase.

## Implementation guidance
Recommended reporting architecture:
- operational tables in Postgres remain source of truth
- reporting views or star-schema tables are built from operational tables
- Power BI or equivalent consumes reporting views
- tooltip data comes from the same reporting model, not a separate ad hoc query source

Recommended semantic model entities:
- `dim_date`
- `dim_call_type`
- `dim_campaign`
- `fact_call_activity`
- `fact_kpi_daily`
- `fact_kpi_weekly`

## Risks and mitigations
### Risk: inconsistent KPI definitions across visuals
Mitigation: centralize KPI formulas in reporting views or semantic model measures.

### Risk: tooltip values disagree with visible chart bucket
Mitigation: tooltip queries/measures must share the same date bucket and filter context as the parent visual.

### Risk: dashboard depends on Google Sheets during cutover
Mitigation: only mirror Sheets into Postgres; BI reads Postgres-derived reporting tables only.

### Risk: drill-down becomes slow
Mitigation: pre-aggregate daily and weekly facts and index date/call-type filters.

