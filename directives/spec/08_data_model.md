# spec/08_data_model.md

## lead_state
- id
- contact_id
- normalized_phone
- lead_stage
- campaign_name
- ai_campaign
- ai_campaign_value
- last_call_status
- updated_at

## call_events
- id
- call_id
- direction
- status
- end_call_reason
- transcript
- duration_seconds
- recording_url
- start_time_utc
- dedupe_key
- created_at

## classification_results
- id
- call_event_id
- model_used
- prompt_family
- prompt_version
- output_json
- created_at

## summary_results
- id
- call_event_id
- student_summary
- summary_offered
- summary_consent
- model_used
- prompt_family
- prompt_version
- created_at

## task_events
- id
- call_event_id
- provider_task_id
- status
- created_at

## scheduled_jobs
- id
- job_type
- entity_type
- entity_id
- run_at
- rq_job_id
- status
- payload_json
- created_at
- updated_at

## shadow_sheet_rows
- id
- sheet_name
- source_row_id
- payload_json
- mirrored_at
- reconciliation_status

## exceptions
- id
- call_event_id
- type
- severity
- status
- context_json
- created_at
- updated_at

## Reporting entities or views
- `dim_date`
- `dim_call_type`
- `dim_campaign`
- `fact_call_activity`
- `fact_kpi_daily`
- `fact_kpi_weekly` (optional if weekly aggregation is materialized)

## Reporting source-of-truth rule
Dashboard metrics shall be computed from Postgres-authoritative reporting views or tables.
Google Sheets shadow data shall not be used as the runtime source for KPI calculations.