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
  LeadTraceResponse,
  MetricsResponse,
  RecentCallsResponse,
  ResolveRequest,
  RetryRequest,
  SaveSettingsRequest,
  SaveSettingsResponse,
  VoicePerformanceResponse,
} from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

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
}): Promise<VoicePerformanceResponse> {
  const params: Record<string, string> = {};
  if (options?.from_date) params.from_date = options.from_date;
  if (options?.to_date) params.to_date = options.to_date;
  return get<VoicePerformanceResponse>("/dashboard/voice-performance", params);
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

export async function fetchSettings(): Promise<AppConfigResponse> {
  return get<AppConfigResponse>("/dashboard/settings");
}

export async function saveSettings(body: SaveSettingsRequest): Promise<SaveSettingsResponse> {
  return post<SaveSettingsResponse>("/dashboard/settings", body);
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
