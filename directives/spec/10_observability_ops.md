# spec/10_observability_ops.md

## Logs
- event receipt
- enrich success/failure
- dedupe decision
- AI job execution
- summary consent result
- task creation result
- tier transition
- callback scheduled
- shadow mirror sync result
- exception created/resolved

## Metrics
- conversion rate
- callback completion rate
- duplicate rate
- task success rate
- summary writeback rate
- exception volume
- queue lag
- dependency error rate
- sheet mirror reconciliation drift count

## Alerts
- GHL auth failure: critical immediately
- duplicate rate spike: warning/critical thresholds
- stuck-call volume spike: warning/critical thresholds
- queue lag breach
- SQL Server write failures
- sheet mirror reconciliation failures above threshold

## Dashboard scope v1
- exception queue
- retry controls
- resolve/ignore controls
- cancel future jobs
- force finalize
- search by call_id / phone / contact_id
- health tiles for events, failures, stuck calls, duplicates
- sheet mirror reconciliation status

