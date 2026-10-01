/**
 * Typed API client for the Cora Dashboard API v2.
 *
 * All data fetching goes through this module — no direct fetch() in component files.
 * Base URL is configured via NEXT_PUBLIC_API_URL (default: http://localhost:8001).
 *
 * Auth token for write endpoints is supplied via the X-Dashboard-Token header.
 * Set NEXT_PUBLIC_DASHBOARD_TOKEN in .env.local for local development.
 */

import type {
  ActionResponse,
  AiTimeSeriesResponse,
  AlertsResponse,
  AppConfigResponse,
  BulkIgnoreRequest,
  CampaignOverviewResponse,
  CancelRequest,
  CardMetricsResponse,
  EventsResponse,
  ExceptionAnomaliesResponse,
  ExceptionsResponse,
  ExceptionTrendResponse,
  FinalizeRequest,
  HealthResponse,
  IgnoreRequest,
  IntentCallsResponse,
  LeadDetailResponse,
  LeadLifecycleResponse,
  LeadTraceResponse,
  MetricsResponse,
  RecentCallsResponse,
  ResolveRequest,
  RetryRequest,
  SalesOutcomeRequest,
  SalesOutcomeResponse,
  SaveSettingsRequest,
  SaveSettingsResponse,
  StaffCallQualityResponse,
  TagAiColdLeadsRunsResponse,
  VoicePerformanceResponse,
  WebhookFailuresResponse,
  WorkerActivityResponse,
  WorkerTrendResponse,
} from "@/types";

// Server-side (SSR/RSC): use full internal URL to reach dashboard-api directly.
// Browser (client components): use same origin so Next.js rewrites proxy the
// request to dashboard-api — avoids CORS entirely and works at any server IP.
const API_URL =
  typeof window === "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001")
    : window.location.origin;

// ── Request helpers ───────────────────────────────────────────────────────────

function authHeaders(): HeadersInit {
  const token =
    typeof window !== "undefined"
      ? localStorage.getItem("dashboard_token")
      : process.env.NEXT_PUBLIC_DASHBOARD_TOKEN ?? "";
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(path, API_URL);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    });
  }
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const url = new URL(path, API_URL);
  const res = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

// ── Endpoint wrappers ─────────────────────────────────────────────────────────

export async function fetchHealth(): Promise<HealthResponse> {
  return get<HealthResponse>("/dashboard/health");
}

export async function fetchWorkerActivity(): Promise<WorkerActivityResponse> {
  return get<WorkerActivityResponse>("/dashboard/worker-activity");
}

export async function fetchWorkerActivityTrend(): Promise<WorkerTrendResponse> {
  return get<WorkerTrendResponse>("/dashboard/worker-activity-trend");
}

export async function fetchMetrics(options?: {
  campaign?: string;
  direction?: string;
  voice_agent?: string;
  from_date?: string;
  to_date?: string;
}): Promise<MetricsResponse> {
  const params: Record<string, string> = {};
  if (options?.campaign)    params.campaign    = options.campaign;
  if (options?.direction)   params.direction   = options.direction;
  if (options?.voice_agent) params.voice_agent = options.voice_agent;
  if (options?.from_date)   params.from_date   = options.from_date;
  if (options?.to_date)     params.to_date     = options.to_date;
  return get<MetricsResponse>("/dashboard/metrics", params);
}

export async function fetchEvents(options?: {
  since?: string;
  limit?: number;
}): Promise<EventsResponse> {
  const params: Record<string, string> = {};
  if (options?.since) params.since = options.since;
  if (options?.limit) params.limit = String(options.limit);
  return get<EventsResponse>("/dashboard/events", params);
}

export async function fetchLeadTrace(
  contactId: string,
  phone?: string
): Promise<LeadTraceResponse> {
  const params: Record<string, string> = {};
  if (phone) params.phone = phone;
  return get<LeadTraceResponse>(`/dashboard/lead/${encodeURIComponent(contactId)}/trace`, params);
}

export async function fetchAlerts(status?: "active" | "resolved" | "acknowledged"): Promise<AlertsResponse> {
  const params: Record<string, string> = {};
  if (status) params.status = status;
  return get<AlertsResponse>("/dashboard/alerts", params);
}

export async function fetchExceptions(options?: {
  status?: "open" | "resolved" | "ignored";
  severity?: string;
  type?: string;
  from_date?: string;
  to_date?: string;
  limit?: number;
  offset?: number;
}): Promise<ExceptionsResponse> {
  const params: Record<string, string> = {};
  if (options?.status)     params.status     = options.status;
  if (options?.severity)   params.severity   = options.severity;
  if (options?.type)       params.type       = options.type;
  if (options?.from_date)  params.from_date  = options.from_date;
  if (options?.to_date)    params.to_date    = options.to_date;
  if (options?.limit)      params.limit      = String(options.limit);
  if (options?.offset)     params.offset     = String(options.offset);
  return get<ExceptionsResponse>("/dashboard/exceptions", params);
}

export async function fetchExceptionTrend(options?: {
  from_date?: string;
  to_date?: string;
}): Promise<ExceptionTrendResponse> {
  const params: Record<string, string> = {};
  if (options?.from_date) params.from_date = options.from_date;
  if (options?.to_date)   params.to_date   = options.to_date;
  return get<ExceptionTrendResponse>("/dashboard/exceptions/trend", params);
}

export async function fetchExceptionAnomalies(): Promise<ExceptionAnomaliesResponse> {
  return get<ExceptionAnomaliesResponse>("/dashboard/exceptions/anomalies");
}

export async function fetchAiTimeSeries(options?: {
  from_date?: string;
  to_date?: string;
}): Promise<AiTimeSeriesResponse> {
  const params: Record<string, string> = {};
  if (options?.from_date) params.from_date = options.from_date;
  if (options?.to_date) params.to_date = options.to_date;
  return get<AiTimeSeriesResponse>("/dashboard/ai-timeseries", params);
}

export async function fetchVoicePerformance(options?: {
  from_date?: string;
  to_date?: string;
  all_time?: boolean;
  wow_mode?: boolean;
  wow_shift?: boolean;
}): Promise<VoicePerformanceResponse> {
  const params: Record<string, string> = {};
  if (options?.from_date) params.from_date = options.from_date;
  if (options?.to_date) params.to_date = options.to_date;
  if (options?.all_time) params.all_time = "true";
  if (options?.wow_mode) params.wow_mode = "true";
  if (options?.wow_shift) params.wow_shift = "true";
  return get<VoicePerformanceResponse>("/dashboard/voice-performance", params);
}

export async function fetchVoicePerformanceEarliestDate(): Promise<string | null> {
  const res = await get<{ monday: string | null }>("/dashboard/voice-performance/earliest-date");
  return res.monday;
}

export async function fetchCampaignOverview(options?: {
  from_date?: string;
  to_date?: string;
}): Promise<CampaignOverviewResponse> {
  const params: Record<string, string> = {};
  if (options?.from_date) params.from_date = options.from_date;
  if (options?.to_date) params.to_date = options.to_date;
  return get<CampaignOverviewResponse>("/dashboard/campaign-overview", params);
}

export async function fetchLeadDetail(contactId: string): Promise<LeadDetailResponse> {
  return get<LeadDetailResponse>(`/dashboard/lead/${encodeURIComponent(contactId)}/detail`);
}

export async function saveSalesOutcome(body: SalesOutcomeRequest): Promise<SalesOutcomeResponse> {
  return post<SalesOutcomeResponse>("/dashboard/sales-queue/outcome", body);
}

export async function fetchSettings(): Promise<AppConfigResponse> {
  return get<AppConfigResponse>("/dashboard/settings");
}

export async function saveSettings(body: SaveSettingsRequest): Promise<SaveSettingsResponse> {
  return post<SaveSettingsResponse>("/dashboard/settings", body);
}

// ── Mode control ──────────────────────────────────────────────────────────────

export interface ModeFlags {
  shadow_mode_enabled: boolean;
  ghl_write_mode: "shadow" | "live";
  ghl_write_shadow_log_only: boolean;
  ghl_write_contact_fields: boolean;
  ghl_write_tasks: boolean;
  ghl_write_summary: boolean;
  ghl_write_campaign_state: boolean;
  ghl_write_finalization: boolean;
  system_paused: boolean;
  outbound_campaigns_paused: boolean;
  cold_lead_campaign_paused: boolean;
  ghl_writes_enabled: boolean;
}

export interface PreflightCheck {
  key: string;
  label: string;
  status: "ok" | "warning" | "error";
  detail: string;
}

export interface LastChanged {
  operator_id: string;
  at: string | null;
  new_value: string;
}

export interface ModeResponse {
  flags: ModeFlags;
  last_changed: Record<string, LastChanged>;
  preflight: PreflightCheck[];
}

export interface UpdateModeRequest {
  flags: Record<string, string>;
  reason?: string;
}

export interface UpdateModeResponse {
  status: string;
  keys_updated: string[];
  flags: ModeFlags;
}

export async function fetchMode(): Promise<ModeResponse> {
  return get<ModeResponse>("/dashboard/mode");
}

export async function updateMode(body: UpdateModeRequest): Promise<UpdateModeResponse> {
  return post<UpdateModeResponse>("/dashboard/mode", body);
}

export async function pauseSystem(): Promise<{ status: string; system_paused: boolean }> {
  return post("/dashboard/mode/pause", {});
}

export async function resumeSystem(): Promise<{ status: string; system_paused: boolean }> {
  return post("/dashboard/mode/resume", {});
}

export async function pauseOutboundCampaigns(): Promise<{ status: string; outbound_campaigns_paused: boolean }> {
  return post("/dashboard/mode/pause-outbound-campaigns", {});
}

export async function resumeOutboundCampaigns(): Promise<{ status: string; outbound_campaigns_paused: boolean }> {
  return post("/dashboard/mode/resume-outbound-campaigns", {});
}

export async function pauseColdLeadCampaign(): Promise<{ status: string; cold_lead_campaign_paused: boolean }> {
  return post("/dashboard/mode/pause-cold-lead-campaign", {});
}

export async function resumeColdLeadCampaign(): Promise<{ status: string; cold_lead_campaign_paused: boolean }> {
  return post("/dashboard/mode/resume-cold-lead-campaign", {});
}

// ── Operator actions ──────────────────────────────────────────────────────────

export async function retryException(body: RetryRequest): Promise<ActionResponse> {
  return post<ActionResponse>("/dashboard/actions/retry", body);
}

export async function cancelLeadJobs(body: CancelRequest): Promise<ActionResponse> {
  return post<ActionResponse>("/dashboard/actions/cancel", body);
}

export async function finalizeLead(body: FinalizeRequest): Promise<ActionResponse> {
  return post<ActionResponse>("/dashboard/actions/finalize", body);
}

export async function resolveException(body: ResolveRequest): Promise<ActionResponse> {
  return post<ActionResponse>("/dashboard/actions/resolve", body);
}

export async function ignoreException(body: IgnoreRequest): Promise<ActionResponse> {
  return post<ActionResponse>("/dashboard/actions/ignore", body);
}

export async function bulkIgnoreExceptions(body: BulkIgnoreRequest): Promise<ActionResponse & { ignored_count: number }> {
  return post<ActionResponse & { ignored_count: number }>("/dashboard/actions/bulk-ignore", body);
}

export async function acknowledgeAlert(alertId: string, note = ""): Promise<{ status: string; alert_id: string; audit_log_id: string | null }> {
  return post("/dashboard/actions/acknowledge-alert", { alert_id: alertId, note });
}

export async function fetchCardMetrics(): Promise<CardMetricsResponse> {
  return get<CardMetricsResponse>("/dashboard/card-metrics");
}

export async function fetchRecentCalls(options?: {
  from_date?: string;
  to_date?: string;
  voice_agent?: string;
  limit?: number;
}): Promise<RecentCallsResponse> {
  const params: Record<string, string> = {};
  if (options?.from_date)    params.from_date    = options.from_date;
  if (options?.to_date)      params.to_date      = options.to_date;
  if (options?.voice_agent)  params.voice_agent  = options.voice_agent;
  if (options?.limit)        params.limit        = String(options.limit);
  return get<RecentCallsResponse>("/dashboard/recent-calls", params);
}

export async function fetchStaffCallQuality(options?: {
  limit?: number;
  from_date?: string;
  to_date?: string;
}): Promise<StaffCallQualityResponse> {
  const params: Record<string, string> = {};
  if (options?.limit)     params.limit     = String(options.limit);
  if (options?.from_date) params.from_date = options.from_date;
  if (options?.to_date)   params.to_date   = options.to_date;
  return get<StaffCallQualityResponse>("/dashboard/staff-call-quality", params);
}

export async function fetchTagAiColdLeadsRuns(limit?: number): Promise<TagAiColdLeadsRunsResponse> {
  const params: Record<string, string> = {};
  if (limit) params.limit = String(limit);
  return get<TagAiColdLeadsRunsResponse>("/dashboard/tag-ai-cold-leads/runs", params);
}

export async function fetchIntentCalls(options: {
  intent: string;
  from_date?: string;
  to_date?: string;
  campaign?: string;
  voice_agent?: string;
  direction?: string;
  limit?: number;
}): Promise<IntentCallsResponse> {
  const params: Record<string, string> = { intent: options.intent };
  if (options.from_date)   params.from_date   = options.from_date;
  if (options.to_date)     params.to_date     = options.to_date;
  if (options.campaign)    params.campaign    = options.campaign;
  if (options.voice_agent) params.voice_agent = options.voice_agent;
  if (options.direction)   params.direction   = options.direction;
  if (options.limit)       params.limit       = String(options.limit);
  return get<IntentCallsResponse>("/dashboard/intent-calls", params);
}

export async function fetchDbTables(): Promise<{ tables: { name: string; row_estimate: number }[] }> {
  return get("/dashboard/db/tables");
}

export async function runDbQuery(sql: string): Promise<{
  columns: string[];
  rows: (string | null)[][];
  row_count: number;
  truncated: boolean;
}> {
  return post("/dashboard/db/query", { sql });
}

export async function fetchWebhookFailures(): Promise<WebhookFailuresResponse> {
  return get<WebhookFailuresResponse>("/dashboard/webhook-failures");
}

export async function advanceStaleLeadAction(
  contactId: string,
  outcome: "voicemail" | "no_answer",
): Promise<{ status: string; action: string; tier_from?: string; tier_to?: string; run_at?: string; reason?: string; last_call_status?: string }> {
  return post("/dashboard/actions/advance-stale-lead", { contact_id: contactId, outcome });
}

export async function recoverCallWebhook(
  contactId: string,
  callId: string,
): Promise<{ status: string; action: string; synthflow_call_id: string; call_status: string; campaign_name: string }> {
  return post("/dashboard/actions/recover-call-webhook", { contact_id: contactId, call_id: callId });
}

export async function ignoreWebhookFailure(
  jobId: string,
  contactId: string,
): Promise<{ status: string }> {
  return post("/dashboard/actions/ignore-webhook-failure", { job_id: jobId, contact_id: contactId });
}

export async function fetchLeadLifecycle(options?: {
  status?:   "all" | "active" | "stale" | "finalized" | "vm" | "dnc";
  campaign?: string;
  limit?:    number;
  offset?:   number;
}): Promise<LeadLifecycleResponse> {
  const params: Record<string, string> = {};
  if (options?.status)   params.status   = options.status;
  if (options?.campaign) params.campaign = options.campaign;
  if (options?.limit)    params.limit    = String(options.limit);
  if (options?.offset)   params.offset   = String(options.offset);
  return get<LeadLifecycleResponse>("/dashboard/lead-lifecycle", params);
}

// ── Wrong Date Monitor (spec/32) ──────────────────────────────────────────────

export interface WrongDateItem {
  kind: "class" | "open_house";
  raw: string;
  expected: string;
}

export interface WrongDateIncident {
  id: string;
  contact_id: string;
  channel: "sms" | "email";
  wrong_dates: WrongDateItem[];
  snippet: string;
  expected_class_start: string;
  expected_open_house: string;
  message_sent_at: string | null;
  status: "open" | "corrected" | "dismissed";
  resolved_by: string | null;
  resolved_at: string | null;
  correction_text: string | null;
  created_at: string | null;
}

export interface WrongDatesResponse {
  expected: { class_start: string; open_house: string };
  open_count: number;
  stats: { open: number; closed_24h: number; corrected_24h: number; dismissed_24h: number; new_24h: number };
  correction_preview: string | null;
  correction_previews: { email: string; sms: string };
  correction_email_subject: string;
  sms_enabled: boolean;
  test_available: boolean;
  email_daily_cap: number;
  email_sent_last_24h: number;
  open_leads: number;
  bulk_send_max: number;
  settings_changed_at: string | null;
  open_before_settings_change: number;
  incidents: WrongDateIncident[];
}

export interface OptoutRow {
  id: string; contact_id: string; source: string; kind: string; scope: string; confidence: string;
  decided_by: string; status: string; phrase: string | null; excerpt: string | null; reason: string | null;
  created_at: string | null; resolved_at: string | null;
}

export interface OptoutsResponse {
  counts: Record<string, number>; review_by_source: Record<string, number>;
  review: OptoutRow[]; applied: OptoutRow[]; failed: OptoutRow[]; reconcile_pending: number;
}

export async function fetchOptouts(): Promise<OptoutsResponse> {
  return get<OptoutsResponse>("/dashboard/optouts");
}

export async function applyOptout(action_id: string, scope?: string[]): Promise<{ status: string }> {
  return post("/dashboard/actions/optout-apply", { action_id, scope });
}

export async function dismissOptout(action_id: string): Promise<{ status: string }> {
  return post("/dashboard/actions/optout-dismiss", { action_id });
}

export async function undoOptout(action_id: string): Promise<{ status: string }> {
  return post("/dashboard/actions/optout-undo", { action_id });
}

export async function applyOptoutBatch(source = "reconcile"): Promise<{ applied: number; failed: number; remaining: number }> {
  return post("/dashboard/actions/optout-apply-batch", { source });
}

export interface DeliveryChannel {
  channel: "email" | "sms" | "call"; label: string; level: "green" | "amber" | "red" | "grey"; reasons: string[];
  sent: number; delivered: number; failed: number; unconfirmed: number; rate: number | null;
  ghl_delivered_24h: number | null; last_delivered_at: string | null; last_handoff_at: string | null;
  muted: string | null; trend: { day: string; sent: number; delivered: number }[];
}

export interface DeliveryHealthResponse {
  channels: DeliveryChannel[];
  replies: { last_24h: number; last_at: string | null; by_channel: Record<string, number> };
  worst: "green" | "amber" | "red" | "grey"; silence_hours: number; confirm_minutes: number;
  sync: { last_run_at: string | null; last_error: string | null; backfill_done: boolean };
}

export interface DeliveryDetailResponse {
  channel: string; items: { at: string; contact_id: string; state: string; error: string | null }[];
}

export async function fetchDeliveryHealth(): Promise<DeliveryHealthResponse> {
  return get<DeliveryHealthResponse>("/dashboard/delivery-health");
}

export async function fetchDeliveryDetail(channel: string): Promise<DeliveryDetailResponse> {
  return get<DeliveryDetailResponse>(`/dashboard/delivery-health/${channel}`);
}

export interface SmsMonitorRow {
  at: string; source: string; status: string; code: string; segments: number;
  contact_id: string; reason: string; preview: string;
}

export interface SmsMonitorResponse {
  day_resets_at: string; daily_cap: number; hard_max: number; segments_today: number; remaining: number;
  level: "ok" | "warning" | "exhausted"; messages_today: number; blocked_today: number;
  deferred_today: number; failed_today: number; by_source: Record<string, number>;
  by_hour_pacific: Record<string, number>;
  limits: { min_gap_seconds: number; per_minute_cap: number; max_segments_per_message: number };
  recent: SmsMonitorRow[];
}

export async function fetchSmsMonitor(): Promise<SmsMonitorResponse> {
  return get<SmsMonitorResponse>("/dashboard/sms-monitor");
}

export async function fetchWrongDates(
  status: "open" | "corrected" | "dismissed" = "open",
): Promise<WrongDatesResponse> {
  return get<WrongDatesResponse>("/dashboard/wrong-dates", { status });
}

export type CorrectionChannel = "email" | "sms";

export async function sendDateCorrection(
  incidentId: string,
  channel: CorrectionChannel = "email",
): Promise<{
  status: "sent" | "shadow" | "skipped" | "held";
  correction_text: string;
  also_closed?: number;
  reason?: string;
  next_open?: string | null;
}> {
  return post("/dashboard/actions/send-date-correction", { incident_id: incidentId, channel });
}

export async function dismissWrongDate(incidentId: string, note = ""): Promise<{ status: string }> {
  return post("/dashboard/actions/dismiss-wrong-date", { incident_id: incidentId, note });
}

export async function dismissWrongDatesBulk(note = ""): Promise<{ status: string; dismissed: number; cutoff: string }> {
  return post("/dashboard/actions/dismiss-wrong-dates-bulk", { note });
}

export interface BulkSendResult {
  status: "shadow" | "done";
  shadow: boolean;
  sent: number;
  failed: number;
  remaining: number;
  total_leads: number;
  stopped_early?: boolean;
  errors?: string[];
  blocked?: number;               // skipped: DND / STOP / opt-out / do-not-contact (incident closed)
  held?: number;                  // not sent now: outside the sending window or lead replied (stays open)
  next_window_opens?: string | null;
  daily_cap?: number;
  daily_cap_reached?: boolean;
  sent_last_24h?: number;
}

export async function sendDateCorrectionAll(channel: CorrectionChannel = "email"): Promise<BulkSendResult> {
  return post("/dashboard/actions/send-date-correction-all", { channel });
}

export async function sendTestCorrection(
  channel: CorrectionChannel,
): Promise<{ status: "sent" | "skipped" | "shadow"; reason?: string; correction_text?: string }> {
  return post("/dashboard/actions/send-test-correction", { channel });
}
