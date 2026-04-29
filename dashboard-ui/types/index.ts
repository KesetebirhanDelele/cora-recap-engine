/**
 * TypeScript types for the Cora Dashboard API v2.
 * All shapes match the response schemas in directives/spec/dashboard/07_api_contracts.md.
 * All timestamps are ISO 8601 UTC strings.
 */

// ── Worker Activity ───────────────────────────────────────────────────────────

export interface WorkerJobBreakdown {
  job_type: string;
  count: number;
  avg_duration_s: number | null;
}

export interface WorkerSummary {
  worker_id: string;
  worker_id_short: string;
  is_active: boolean;
  current_job_type: string | null;
  jobs_last_10m: number;
  avg_duration_s: number | null;
  breakdown: WorkerJobBreakdown[];
}

export interface WorkerActivityResponse {
  workers: WorkerSummary[];
  window_minutes: number;
  recorded_at: string;
}

// ── Worker Activity Trend ─────────────────────────────────────────────────────

export interface WorkerTrendPoint {
  bucket: string;
  worker_id: string;
  worker_id_short: string;
  jobs: number;
  median_duration_s: number | null;
}

export interface WorkerTrendWorker {
  worker_id: string;
  worker_id_short: string;
}

export interface WorkerTrendResponse {
  points: WorkerTrendPoint[];
  worker_ids: WorkerTrendWorker[];
  window_minutes: number;
  bucket_minutes: number;
  recorded_at: string;
}

// ── Health ────────────────────────────────────────────────────────────────────

export interface HealthResponse {
  queue_lag_seconds: number;
  active_workers: number;
  open_exception_count: number;
  today_exception_count: number;
  resolved_last_24h: number;
  stuck_job_count: number;
  expired_lease_count: number;
  jobs_completed_last_5m: number;
  jobs_failed_last_5m: number;
  error_rate: number | null;
  shadow_mode_enabled: boolean;
  ghl_write_mode: string;
  app_env: string;
  recorded_at: string;
}

// ── Metrics ───────────────────────────────────────────────────────────────────

export interface StuckJob {
  job_id: string;
  job_type: string;
  contact_id: string | null;
  run_at: string;
  lag_seconds: number;
}

export interface ExpiredLease {
  job_id: string;
  job_type: string;
  worker_id: string;
  age_seconds: number;
}

export interface MetricsResponse {
  period: { from: string; to: string };
  campaign_filter: string | null;
  kpis: {
    total_calls: number;
    pickup_rate: number | null;
    voicemail_rate: number | null;
    failed_rate: number | null;
    do_not_call_rate: number | null;
    enrolled_count: number;
  };
  ai: {
    blank_transcript_rate: number | null;
    intent_distribution: Record<string, number>;
    consent_distribution: Record<string, number>;
  };
  queue: {
    stuck_jobs: StuckJob[];
    expired_leases: ExpiredLease[];
  };
  crm: {
    ghl_task_success_rate: number | null;
    ghl_vm_update_success_rate: number | null;
    ghl_shadow_write_count: number;
  };
}

// ── Events ────────────────────────────────────────────────────────────────────

export interface StreamEvent {
  id: string;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  contact_id: string | null;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface EventsResponse {
  events: StreamEvent[];
  next_cursor: string | null;
}

// ── Pipeline trace ────────────────────────────────────────────────────────────

export interface TraceStep {
  job_id: string;
  job_type: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  status: "pending" | "claimed" | "running" | "completed" | "failed" | "cancelled";
  is_shadow: boolean;
  shadow_payload: Record<string, unknown> | null;
  exception_id: string | null;
  failure_reason: string | null;
  payload_summary: Record<string, unknown>;
}

export interface LeadTraceResponse {
  contact_id: string;
  normalized_phone: string | null;
  campaign_name: string | null;
  status: string;
  ai_campaign_value: string | null;
  steps: TraceStep[];
}

// ── Alerts ────────────────────────────────────────────────────────────────────

export type AlertSeverity = "critical" | "warning";
export type AlertStatus = "active" | "resolved" | "acknowledged";

export interface Alert {
  id: string;
  alert_type: string;
  severity: AlertSeverity;
  status: AlertStatus;
  current_value: number | null;
  threshold: number | null;
  message: string;
  email_sent_at: string | null;
  last_seen_at: string;
  resolved_at: string | null;
  created_at: string;
}

export interface AlertsResponse {
  alerts: Alert[];
}

// ── Exceptions ────────────────────────────────────────────────────────────────

export type ExceptionStatus = "open" | "resolved" | "ignored";
export type ExceptionSeverity = "critical" | "warning";

export interface ExceptionRecord {
  id: string;
  call_event_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  type: string;
  severity: ExceptionSeverity;
  status: ExceptionStatus;
  resolution_reason: string | null;
  resolved_by: string | null;
  context_json: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ExceptionGroup {
  type: string;
  severity: ExceptionSeverity;
  count: number;
}

export interface ExceptionsResponse {
  exceptions: ExceptionRecord[];
  total: number;
  status_filter: string;
  groups: ExceptionGroup[];
}

// ── Exception Trend ───────────────────────────────────────────────────────────

export interface ExceptionTrendPoint {
  date: string;
  type: string;
  count: number;
}

export interface ExceptionTrendResponse {
  period: { from: string; to: string };
  points: ExceptionTrendPoint[];
}

// ── Exception Anomalies ───────────────────────────────────────────────────────

export interface ExceptionSpike {
  type: string;
  recent_24h: number;
  baseline_daily_avg: number;
  spike_factor: number | null;
  is_new_type: boolean;
}

export interface ExceptionRecurring {
  type: string;
  severity: string;
  total: number;
  open: number;
  resolved: number;
  first_seen: string;
  last_seen: string;
}

export interface ExceptionCluster {
  entity_id: string;
  entity_type: string | null;
  failure_count: number;
  exception_types: string[];
  last_failure: string;
}

export interface ExceptionAnomaliesResponse {
  spikes: ExceptionSpike[];
  recurring: ExceptionRecurring[];
  clusters: ExceptionCluster[];
  anomaly_trend: { date: string; count: number }[];
  computed_at: string;
}

// ── Voice Performance ─────────────────────────────────────────────────────────

export interface VoiceKpis {
  unique_contacts: number;
  booked_appts: number;
  calls_per_day: number;
  completion_rate: number | null;
  avg_call_duration_sec: number;
  pickup_rate: number | null;
  voicemail_rate: number | null;
  failed_rate: number | null;
  total_calls: number;
  booking_rate: number | null;
}

export interface CampaignWeekStats {
  total_calls: number;
  unique_contacts: number;
  booked_appts: number;
  calls_per_day: number;
  completion_rate: number | null;
  pickup_rate: number | null;
  voicemail_rate: number | null;
  failed_rate: number | null;
  booking_rate: number | null;
  avg_call_duration_sec: number;
}

export interface VoiceTimeSeriesPoint {
  date: string;
  cold: number;
  inbound: number;
  new_lead: number;
  cold_unique: number;
  inbound_unique: number;
  new_lead_unique: number;
  completion_rate: number;
  pickup_rate: number;
  voicemail_rate: number;
  failed_rate: number;
  booked_appts: number;
  booking_rate: number;
  unique_contacts: number;
  calls_per_day: number;
  avg_call_duration_sec: number;
  cold_stats?: CampaignWeekStats;
  inbound_stats?: CampaignWeekStats;
  new_lead_stats?: CampaignWeekStats;
}

export interface VoiceCampaignBreakdown {
  campaign: string;
  total_calls: number;
  pickup_rate: number;
  booking_rate: number;
  avg_calls_per_day: number;
}

export interface VoicePerformanceResponse {
  period: { from: string; to: string };
  kpis: VoiceKpis;
  kpis_prev: VoiceKpis;
  wow_changes: Record<string, number | null>;
  time_series: VoiceTimeSeriesPoint[];
  campaign_breakdown: VoiceCampaignBreakdown[];
}

// ── AI Timeseries ─────────────────────────────────────────────────────────────

export interface AiTimeSeriesPoint {
  date: string;
  total_calls: number;
  blank_transcript_rate: number | null;
  unknown_intent_rate: number | null;
  intent_distribution: Record<string, number>;
}

export interface AiTimeSeriesResponse {
  period: { from: string; to: string };
  time_series: AiTimeSeriesPoint[];
}

// ── Campaign Overview ─────────────────────────────────────────────────────────

export interface CampaignOverviewRow {
  contact_id: string;
  contact: string;
  campaign_name: string;
  last_call_at: string | null;
  next_action: string | null;
  status: string | null;
  sales_outcome: string | null;
  sales_next_action: string | null;
  sales_follow_up_at: string | null;
  sales_updated_by: string | null;
}

// ── Sales Queue ───────────────────────────────────────────────────────────────

export interface SalesOutcomeRequest {
  contact_id: string;
  sales_outcome: string;
  sales_next_action?: string | null;
  sales_follow_up_at?: string | null;
  updated_by: string;
  sales_notes?: string | null;
}

export interface SalesOutcomeResponse {
  status: string;
  contact_id: string;
  sales_outcome: string;
  is_terminal: boolean;
}

export interface CampaignOverviewResponse {
  from_date: string;
  to_date: string;
  rows: CampaignOverviewRow[];
  total: number;
}

// ── Lead Detail (full 6-table drill-down) ─────────────────────────────────────

export interface LeadStateRecord {
  contact_id: string;
  campaign_name: string | null;
  ai_campaign_value: string | null;
  status: string;
  do_not_call: boolean;
  next_action_at: string | null;
  version: number;
  updated_at: string | null;
}

export interface CallEventRecord {
  call_id: string;
  status: string;
  duration_seconds: number | null;
  transcript_preview: string | null;
  created_at: string;
  student_summary: string | null;
}

export interface ShadowActionRecord {
  action_type: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface ScheduledJobRecord {
  job_type: string;
  status: string;
  run_at: string | null;
  payload_json: Record<string, unknown> | null;
  created_at: string;
}

export interface OutboundMessageRecord {
  channel: string;
  status: string;
  subject: string | null;
  body: string | null;
  created_at: string;
}

export interface ContactExceptionRecord {
  type: string;
  severity: string;
  status: string;
  context_json: Record<string, unknown>;
  created_at: string;
}

export interface LeadDetailResponse {
  contact_id: string;
  lead_state: LeadStateRecord | null;
  call_events: CallEventRecord[];
  shadow_actions: ShadowActionRecord[];
  scheduled_jobs: ScheduledJobRecord[];
  outbound_messages: OutboundMessageRecord[];
  exceptions: ContactExceptionRecord[];
}

// ── Settings ──────────────────────────────────────────────────────────────────

export interface AuditLogEntry {
  created_at: string | null;
  operator_id: string;
  key: string;
}

export interface AppConfigResponse {
  config: Record<string, string>;
  audit_log: AuditLogEntry[];
}

export interface SaveSettingsRequest {
  operator_id: string;
  values: Record<string, string>;
}

export interface SaveSettingsResponse {
  status: "ok";
  keys_saved: number;
}

// ── Operator action requests ──────────────────────────────────────────────────

export interface RetryRequest {
  exception_id: string;
  delay_minutes: number;
}

export interface CancelRequest {
  contact_id: string;
  reason: string;
}

export interface FinalizeRequest {
  contact_id: string;
  reason: string;
}

export interface ResolveRequest {
  exception_id: string;
  note?: string;
}

export interface IgnoreRequest {
  exception_id: string;
}

export interface BulkIgnoreRequest {
  type: string;
  note?: string;
}

// ── Operator action responses ─────────────────────────────────────────────────

export interface ActionResponse {
  status: "ok";
  audit_log_id: string | null;
  scheduled_job_id?: string;
  run_at?: string;
  cancelled_job_count?: number;
}

// ── Card Metrics ─────────────────────────────────────────────────────────────

export interface MetricPoint {
  value: number | string | null;
  previous_value: number | string | null;
}

export interface CardMetricsResponse {
  events_per_min:             MetricPoint;
  open_exceptions:            MetricPoint;
  backlog_size:               MetricPoint;
  webhook_delivery_pct:       MetricPoint;
  active_alerts:              MetricPoint;
  lookup_rate:                MetricPoint;
  config_health:              MetricPoint;
  calls_today:                MetricPoint;
  pickup_rate:                MetricPoint;
  meaningful_engagement_rate: MetricPoint;
  booking_rate:               MetricPoint;
  active_leads:               MetricPoint;
  in_vm_sequence:             MetricPoint;
  finalized_today:            MetricPoint;
  sync_success_rate:          MetricPoint;
  anomaly_count:              MetricPoint;
  urgent_leads_count:         MetricPoint;
  computed_at:                string;
}

// ── Recent Calls ──────────────────────────────────────────────────────────────

export interface RecentCallRow {
  contact_id: string;
  lead_name: string;
  phone: string;
  campaign_name: string;
  voice_agent: string | null;
  status: string;
  duration_seconds: number;
  recording_url: string;
  transcript: string;
  transcript_preview: string | null;
  call_time: string; // ISO UTC — frontend converts to CST
  detected_intent: string | null;
  sales_priority: "urgent" | "review" | "none";
  sales_score: number;
  attempts: number;
  last_call_minutes_ago: number | null;
  recommended_action: string;
}

export interface RecentCallsResponse {
  period: { from: string; to: string };
  campaign_filter: string | null;
  total: number;
  calls: RecentCallRow[];
}

// ── Intent Calls Drill-Down ───────────────────────────────────────────────────

export interface IntentCallRow {
  contact_id: string;
  phone: string;
  campaign_name: string;
  status: string;
  duration_seconds: number;
  recording_url: string | null;
  transcript: string | null;
  call_time: string; // ISO UTC — frontend converts to CST
  detected_intent: string;
}

export interface IntentCallsResponse {
  period: { from: string; to: string };
  intent: string;
  campaign_filter: string | null;
  total: number;
  calls: IntentCallRow[];
}

// ── Lead Lifecycle Monitor ────────────────────────────────────────────────────

export interface LeadLifecycleSummary {
  active:            number;
  in_vm_sequence:    number;
  campaign_switched: number;
  finalized:         number;
  avg_days_to_close: number | null;
}

export interface LeadLifecycleRow {
  contact_id:          string;
  lead_name:           string;
  phone:               string | null;
  current_campaign:    string | null;
  initial_campaign:    string | null;
  vm_tier:             string | null;  // null | "0" | "1" | "2" | "3"
  status:              string | null;
  do_not_call:         boolean;
  first_contact_at:    string | null;
  last_contact_at:     string | null;
  days_active:         number | null;
  total_calls:         number;
  total_sms:           number;
  total_email:         number;
  last_intent:         string | null;
  campaign_switches:   number;
  next_job_type:       string | null;
  next_run_at:         string | null;
  finalization_reason: string | null;
  finalized_at:        string | null;
}

export interface LeadLifecycleResponse {
  summary: LeadLifecycleSummary;
  total:   number;
  rows:    LeadLifecycleRow[];
  filters: { status: string; campaign: string };
}

// ── WebSocket messages ────────────────────────────────────────────────────────

export type WebSocketMessage =
  | StreamEvent
  | { type: "error"; code: string; fallback_url: string };

// ── Webhook Failures ──────────────────────────────────────────────────────────

export interface WebhookFailureRow {
  job_id:                  string;
  contact_id:              string | null;
  campaign:                string | null;
  placed_at:               string;
  executed_at:             string;
  minutes_since_execution: number | null;
}

export interface WebhookFailureSummary {
  total_launched: number;
  got_webhook:    number;
  missing:        number;
  webhook_pct:    number | null;
}

export interface WebhookFailuresResponse {
  summary:      WebhookFailureSummary;
  failures:     WebhookFailureRow[];
  window_hours: number;
  recorded_at:  string;
}
