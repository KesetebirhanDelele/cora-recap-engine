"""
Settings loader — Phase 2.

Design rules:
  - All external credentials are Optional so the app boots without them.
  - Context-aware validate_for_*() methods raise ConfigError before an
    integration is used, never at boot time.
  - Empty-string env vars are treated as unset (env_ignore_empty=True).
  - Secrets must never be hard-coded here.

Mode-flag semantics (safe defaults):
  GHL_WRITE_MODE=shadow          → GHL writes are logged, not executed
  GHL_WRITE_SHADOW_LOG_ONLY=true → shadow payloads are log-only (no API call)
  GOOGLE_SHADOW_MODE_ENABLED=true → Sheets mirror is read-only
  SHADOW_MODE_ENABLED=true        → global shadow flag

Idempotency TTL:
  IDEMPOTENCY_TTL_DAYS defaults to 90.
  Rationale: dedupe protection must survive delayed retries, replayed webhook
  deliveries, shadow-mode reconciliation, and operational re-runs across the
  full lifecycle of a call workflow.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when required config is missing before using an integration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,  # treat empty-string env vars as unset → use field default
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str = "cora-recap-engine"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    api_base_url: str = "http://localhost:8000"
    dashboard_base_url: str = "http://localhost:8000/dashboard"
    default_timezone: str = "America/Chicago"
    log_level: str = "INFO"
    secret_key: str = "changeme"
    webhook_shared_secret: str = "changeme"

    # ── Postgres ─────────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_database: str = "cora"
    postgres_username: str = "postgres"
    postgres_password: Optional[str] = None
    database_url: Optional[str] = None
    postgres_pool_size: Optional[int] = None
    postgres_max_overflow: Optional[int] = None
    postgres_echo: bool = False

    # ── Redis / RQ ────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_username: Optional[str] = None
    redis_password: Optional[str] = None
    redis_ssl: bool = False
    redis_url: Optional[str] = None
    rq_default_queue: str = "default"
    rq_ai_queue: str = "ai"
    rq_callback_queue: str = "callbacks"
    rq_retry_queue: str = "retries"
    rq_sheet_mirror_queue: str = "sheet_mirror"
    rq_dashboard_enabled: bool = False

    # ── GHL / LeadConnector ───────────────────────────────────────────────────
    ghl_base_url: str = "https://services.leadconnectorhq.com"
    ghl_api_key: Optional[str] = None
    ghl_location_id: Optional[str] = None
    ghl_timeout_seconds: int = 30
    ghl_retry_max: int = 3
    # When True, conversation_context fetches GHL SMS/email reply history before
    # generating follow-up messages so the AI sees the full two-way thread.
    ghl_fetch_conversation_history: bool = False
    ghl_conversation_history_limit: int = 15

    # GHL field labels / identifiers — unresolved external IDs remain Optional
    ghl_field_ai_campaign: Optional[str] = None
    ghl_field_ai_campaign_value: Optional[str] = None
    ghl_field_ai_lead_classification: Optional[str] = None
    ghl_field_ai_lead_assign_to: Optional[str] = None
    ghl_field_call_detailed_summary: Optional[str] = None
    ghl_field_student_summary: Optional[str] = None
    ghl_field_vm_email_html: Optional[str] = None
    ghl_field_vm_email_subject: Optional[str] = None
    ghl_field_vm_sms_text: Optional[str] = None
    ghl_field_last_call_status: Optional[str] = None
    ghl_field_mark_as_lead: Optional[str] = None
    ghl_field_notes: Optional[str] = None
    ghl_field_support_ticket_2: Optional[str] = None   # VM tier: ticket/message field
    ghl_field_support_ticket_3: Optional[str] = None   # Completed call: task description field
    ghl_field_support_ticket_4: Optional[str] = None   # VM tier: additional field
    ghl_field_message: Optional[str] = None            # VM tier: generated message body
    ghl_task_pipeline_id: Optional[str] = None
    ghl_task_default_owner_id: Optional[str] = None

    # GHL write / shadow controls — default to shadow (safe)
    ghl_write_mode: str = "shadow"
    ghl_write_shadow_log_only: bool = True
    ghl_write_contact_fields: bool = False
    ghl_write_notes: bool = False
    ghl_write_tasks: bool = False
    ghl_write_summary: bool = False
    ghl_write_campaign_state: bool = False
    ghl_write_finalization: bool = False

    # ── GHL Marketplace OAuth app / Conversation Provider (spec/19, spec/20) ──
    # Separate mechanism from ghl_api_key above — a Private Integration token
    # cannot write to GHL Conversations. Used only for call-log writes
    # (recording URL + transcript into a contact's Conversations activity).
    ghl_marketplace_client_id: Optional[str] = None
    ghl_marketplace_client_secret: Optional[str] = None
    ghl_marketplace_shared_secret: Optional[str] = None
    ghl_oauth_redirect_uri: Optional[str] = None
    ghl_conversation_provider_id: Optional[str] = None
    # Target location for the Company->Location token exchange (spec/20 §7).
    # Falls back to ghl_location_id (the Private Integration's location) if
    # unset — in practice this is a single-tenant deployment targeting the
    # same GHL location either way, but kept separate in case that changes.
    ghl_oauth_target_location_id: Optional[str] = None

    # ── GHL InternalComment write path (transcript + recording link) ─────────
    # A THIRD, separate GHL auth mechanism — distinct from both ghl_api_key
    # (spec/16, no Conversations scope) and the OAuth Marketplace app above
    # (spec/19/20, needed only for type="Call" via a registered Conversation
    # Provider). This is a second Private Integration token, scoped with
    # conversations.readonly / conversations/message.readonly /
    # conversations/message.write, used to POST /conversations/messages with
    # type="InternalComment" — confirmed working against both the sandbox and
    # real production accounts on 2026-07-16 without needing a Conversation
    # Provider at all. Delivers transcript + recording link as a staff-only
    # note; does not attach a playable recording (link only, no attachment
    # validation involved).
    ghl_conversations_api_key: Optional[str] = None
    ghl_write_internal_comment: bool = False

    # Independent shadow gate — not tied to ghl_write_mode/ghl_writes_enabled,
    # since this is a completely separate auth mechanism. Default off.
    ghl_write_conversation_log: bool = False

    # ── Synthflow ─────────────────────────────────────────────────────────────
    synthflow_base_url: str = "https://api.synthflow.ai/v2/calls"
    synthflow_api_key: Optional[str] = None
    synthflow_model_id: Optional[str] = None
    synthflow_timeout_seconds: int = 30
    synthflow_retry_max: int = 3
    # Per-campaign "Make Call" Catch Webhook URLs — selected based on lead campaign
    synthflow_launch_workflow_url_new: Optional[str] = None   # New Lead campaign
    synthflow_launch_workflow_url_cold: Optional[str] = None  # Cold Lead campaign
    # Comma-separated phone numbers that must never be dialed as leads.
    # Add Synthflow agent numbers and any other system/test phones here.
    blocked_dial_numbers: Optional[str] = None

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def _normalize_openai_base_url(cls, v: object) -> object:
        """Prepend https:// if the URL is set but missing a protocol prefix.

        Prevents httpx.UnsupportedProtocol when the SDK builds request URLs
        from a bare host like 'api.openai.com/v1'.
        """
        if isinstance(v, str) and v and not v.startswith(("http://", "https://")):
            return "https://" + v
        return v
    openai_model_call_analysis: str = "gpt-4o-mini"
    openai_model_ghl_analysis: str = "gpt-4o-mini"    # GHL call analysis (rich output)
    openai_model_student_summary: str = "gpt-4o-mini"
    openai_model_consent_detector: str = "gpt-4o-mini"
    openai_model_vm_content: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 60
    openai_retry_max: int = 1

    # Prompt registry defaults
    prompt_family_call_analysis: str = "lead_stage_classifier"
    prompt_version_call_analysis: str = "v1"
    prompt_family_student_summary: str = "student_summary_generator"
    prompt_version_student_summary: str = "v1"
    prompt_family_consent: str = "summary_consent_detector"
    prompt_version_consent: str = "v1"
    prompt_family_vm_content: str = "vm_content_generator"
    prompt_version_vm_content: str = "v1"

    # ── Google Sheets shadow mode ─────────────────────────────────────────────
    google_shadow_mode_enabled: bool = True
    google_service_account_json: Optional[str] = None
    google_sheets_call_log_id: Optional[str] = None
    google_sheets_campaign_data_id: Optional[str] = None
    google_sheets_inbound_tab: Optional[str] = None
    google_sheets_new_leads_tab: Optional[str] = None
    google_sheets_cold_leads_tab: Optional[str] = None
    google_sheets_sync_interval_seconds: int = 300

    # ── Business policy ───────────────────────────────────────────────────────
    enable_student_summary_writeback: bool = True
    summary_writeback_requires_consent: bool = True
    task_create_on_completed_call: bool = True
    task_due_date_mode: str = "blank"
    canonical_tier_model: str = "None,0,1,2,3"
    cold_vm_tier_none_delay_minutes: int = 120
    cold_vm_tier_0_delay_minutes: int = 2880
    cold_vm_tier_1_delay_minutes: int = 2880
    cold_vm_tier_2_finalizes: bool = True
    new_vm_tier_none_delay_minutes: Optional[int] = None
    new_vm_tier_0_delay_minutes: Optional[int] = None
    new_vm_tier_1_delay_minutes: Optional[int] = None
    new_vm_tier_2_finalize: Optional[bool] = None
    vm_final_stop_value: int = 3
    # Intent-driven campaign settings
    # nurture_delay_days: days before a re-attempt call for "interested_not_now" leads
    nurture_delay_days: int = 7
    # Messaging follow-up delays after missed calls / voicemails
    sms_followup_delay_minutes: int = 30    # SMS sent N minutes after missed call
    email_followup_delay_days: int = 1      # Email sent N days after 2nd missed call

    # Campaign active windows — outbound calls are deferred outside these windows.
    # Days: comma-separated ISO weekday numbers (0=Monday … 6=Sunday).
    # Hours: 24-hour integers; window is [start_hour, end_hour) exclusive of end.
    # All times apply in default_timezone (default: America/Chicago).
    new_lead_active_days: str = "0,1,2,3,4,5,6"  # all days
    new_lead_active_start_hour: int = 8
    new_lead_active_end_hour: int = 22
    cold_lead_active_days: str = "0,1,2,3,4"     # Mon–Fri only
    cold_lead_active_start_hour: int = 8
    cold_lead_active_end_hour: int = 22

    shadow_mode_enabled: bool = True
    # 90-day default: must survive delayed retries, replayed webhooks,
    # shadow-mode reconciliation, and re-runs across the full call lifecycle.
    idempotency_ttl_days: int = 90
    retention_mode: str = "indefinite"

    # ── Observability ─────────────────────────────────────────────────────────
    sentry_dsn: Optional[str] = None
    otel_exporter_otlp_endpoint: Optional[str] = None
    metrics_enabled: bool = True
    healthcheck_enabled: bool = True
    alert_webhook_url: Optional[str] = None

    # ── Dashboard v2 ──────────────────────────────────────────────────────────
    dashboard_read_auth_required: bool = False
    dashboard_api_url: str = "http://localhost:8001"
    frontend_url: str = "http://localhost:3000"
    ws_url: str = "ws://localhost:8001/ws"
    allow_origins: str = "http://localhost:3000"
    x_operator_id: str = "local@test.com"

    # Email alerting (Gmail SMTP)
    smtp_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    alert_email_from: Optional[str] = None
    alert_email_to: Optional[str] = None  # comma-separated

    # Alert thresholds
    alert_queue_lag_threshold_seconds: int = 300
    alert_error_rate_threshold: float = 0.20
    alert_exception_spike_threshold: int = 10
    alert_dedup_window_seconds: int = 3600

    # Metrics collector
    metrics_collection_interval_seconds: int = 60
    event_stream_retention_days: int = 7
    system_metrics_retention_days: int = 30

    # ─────────────────────────────────────────────────────────────────────────
    # Field validators
    # ─────────────────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def reject_changeme_in_production(self) -> "Settings":
        """Prevent silent misconfiguration: refuse to start in production with default secrets.

        If SECRET_KEY or WEBHOOK_SHARED_SECRET are still 'changeme' and APP_ENV is
        'production', the system would accept forged operator tokens and unauthenticated
        webhooks. Fail fast at boot rather than silently compromising in production.
        """
        if self.app_env == "production":
            bad = []
            if self.secret_key == "changeme":
                bad.append("SECRET_KEY")
            if self.webhook_shared_secret == "changeme":
                bad.append("WEBHOOK_SHARED_SECRET")
            if bad:
                raise ConfigError(
                    f"Production startup blocked: {', '.join(bad)} must not be 'changeme'. "
                    "Set real secret values in your production .env file."
                )
        return self

    @field_validator("ghl_write_mode")
    @classmethod
    def validate_write_mode(cls, v: str) -> str:
        allowed = {"shadow", "live"}
        if v not in allowed:
            raise ValueError(f"ghl_write_mode must be one of {allowed}, got {v!r}")
        return v

    @field_validator("idempotency_ttl_days")
    @classmethod
    def validate_idempotency_ttl(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"idempotency_ttl_days must be >= 1, got {v}")
        return v

    # ─────────────────────────────────────────────────────────────────────────
    # Derived mode-flag helpers
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def is_shadow_mode(self) -> bool:
        """True when GHL writes are in shadow/log-only mode."""
        return self.ghl_write_mode == "shadow" or self.shadow_mode_enabled

    @property
    def ghl_writes_enabled(self) -> bool:
        """True only when write mode is 'live' and shadow_log_only is off."""
        return self.ghl_write_mode == "live" and not self.ghl_write_shadow_log_only

    @property
    def ghl_oauth_effective_target_location_id(self) -> Optional[str]:
        """ghl_oauth_target_location_id, falling back to ghl_location_id."""
        return self.ghl_oauth_target_location_id or self.ghl_location_id

    # ─────────────────────────────────────────────────────────────────────────
    # Context-aware pre-flight validators
    # Call these immediately before using an integration, not at boot.
    # Each raises ConfigError with a clear message so callers can surface it.
    # ─────────────────────────────────────────────────────────────────────────

    def validate_for_ghl_reads(self) -> None:
        """Raise ConfigError if minimum GHL read credentials are missing."""
        missing = []
        if not self.ghl_api_key:
            missing.append("GHL_API_KEY")
        if not self.ghl_location_id:
            missing.append("GHL_LOCATION_ID")
        if missing:
            raise ConfigError(
                f"GHL read integration requires: {', '.join(missing)}"
            )

    def validate_for_ghl_writes(self) -> None:
        """Raise ConfigError if GHL write path cannot be safely used.

        Requires:
          - read credentials (api_key + location_id)
          - write mode must be 'live' (shadow mode blocks writes by design)
          - shadow_log_only must be False
        """
        self.validate_for_ghl_reads()
        if not self.ghl_writes_enabled:
            raise ConfigError(
                "GHL writes are disabled. "
                "Set GHL_WRITE_MODE=live and GHL_WRITE_SHADOW_LOG_ONLY=false "
                "to enable real writes. This change requires explicit approval."
            )

    def validate_for_ghl_marketplace_oauth(self) -> None:
        """Raise ConfigError if the GHL Marketplace OAuth app is not configured.

        Required before exchanging an authorization code, refreshing a token,
        or writing a call-log message to GHL Conversations. Independent of
        validate_for_ghl_writes() — this is a separate auth mechanism.
        """
        missing = []
        if not self.ghl_marketplace_client_id:
            missing.append("GHL_MARKETPLACE_CLIENT_ID")
        if not self.ghl_marketplace_client_secret:
            missing.append("GHL_MARKETPLACE_CLIENT_SECRET")
        if not self.ghl_conversation_provider_id:
            missing.append("GHL_CONVERSATION_PROVIDER_ID")
        if missing:
            raise ConfigError(
                f"GHL Marketplace OAuth integration requires: {', '.join(missing)}"
            )

    def validate_for_ghl_internal_comment(self) -> None:
        """Raise ConfigError if the InternalComment write path is not configured.

        Independent of both validate_for_ghl_writes() (ghl_api_key) and
        validate_for_ghl_marketplace_oauth() (the Conversation Provider app) —
        this is a third, separate Private Integration token.
        """
        if not self.ghl_conversations_api_key:
            raise ConfigError(
                "GHL InternalComment write path requires: GHL_CONVERSATIONS_API_KEY"
            )

    def validate_for_openai(self) -> None:
        """Raise ConfigError if OpenAI credentials are missing or misconfigured."""
        if not self.openai_api_key:
            raise ConfigError("OpenAI integration requires OPENAI_API_KEY")
        if self.openai_base_url and not self.openai_base_url.startswith(
            ("http://", "https://")
        ):
            raise ConfigError(
                f"OPENAI_BASE_URL must start with 'https://' — got: {self.openai_base_url!r}"
            )

    def validate_for_synthflow_read(self) -> None:
        """Raise ConfigError if Synthflow API key is missing (read-only operations)."""
        if not self.synthflow_api_key:
            raise ConfigError("Synthflow integration requires: SYNTHFLOW_API_KEY")

    def validate_for_synthflow(self) -> None:
        """Raise ConfigError if Synthflow credentials are missing (write/launch operations)."""
        missing = []
        if not self.synthflow_api_key:
            missing.append("SYNTHFLOW_API_KEY")
        if not self.synthflow_model_id:
            missing.append("SYNTHFLOW_MODEL_ID")
        if missing:
            raise ConfigError(
                f"Synthflow integration requires: {', '.join(missing)}"
            )

    def get_synthflow_launch_url(self, campaign_name: str) -> str:
        """
        Return the Synthflow Make Call webhook URL for the given campaign.

        Selects SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold for Cold Lead campaigns,
        SYNTHFLOW_LAUNCH_WORKFLOW_URL_New for all others (New Lead, Inbound, etc.).
        Raises ConfigError if the required URL is not configured.
        """
        is_cold = "cold" in (campaign_name or "").lower()
        if is_cold:
            url = self.synthflow_launch_workflow_url_cold
            key = "SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold"
        else:
            url = self.synthflow_launch_workflow_url_new
            key = "SYNTHFLOW_LAUNCH_WORKFLOW_URL_New"
        if not url:
            raise ConfigError(
                f"Synthflow outbound call launch requires {key} (campaign={campaign_name!r})"
            )
        return url

    def validate_for_synthflow_launch(self, campaign_name: str = "") -> None:
        """Raise ConfigError if the Make Call workflow URL for the campaign is not configured."""
        self.get_synthflow_launch_url(campaign_name)

    def validate_for_sheets_sync(self) -> None:
        """Raise ConfigError if Google Sheets shadow sync cannot be initialized.

        All three spreadsheet IDs and at least one tab name must be present.
        """
        missing = []
        if not self.google_service_account_json:
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not self.google_sheets_call_log_id:
            missing.append("GOOGLE_SHEETS_CALL_LOG_ID")
        if not self.google_sheets_campaign_data_id:
            missing.append("GOOGLE_SHEETS_CAMPAIGN_DATA_ID")
        if not any([
            self.google_sheets_inbound_tab,
            self.google_sheets_new_leads_tab,
            self.google_sheets_cold_leads_tab,
        ]):
            missing.append("at least one of GOOGLE_SHEETS_*_TAB")
        if missing:
            raise ConfigError(
                f"Google Sheets sync requires: {', '.join(missing)}"
            )

    def validate_for_new_lead_vm_policy(self) -> None:
        """Raise ConfigError if New Lead voicemail tier delays are unresolved."""
        missing = []
        if self.new_vm_tier_none_delay_minutes is None:
            missing.append("NEW_VM_TIER_NONE_DELAY_MINUTES")
        if self.new_vm_tier_0_delay_minutes is None:
            missing.append("NEW_VM_TIER_0_DELAY_MINUTES")
        if self.new_vm_tier_1_delay_minutes is None:
            missing.append("NEW_VM_TIER_1_DELAY_MINUTES")
        if self.new_vm_tier_2_finalize is None:
            missing.append("NEW_VM_TIER_2_FINALIZE")
        if missing:
            raise ConfigError(
                f"New Lead VM tier policy requires: {', '.join(missing)}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
