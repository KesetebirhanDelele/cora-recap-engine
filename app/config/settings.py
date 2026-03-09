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

from pydantic import field_validator
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
    postgres_port: int = 5432
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

    # ── Synthflow ─────────────────────────────────────────────────────────────
    synthflow_base_url: str = "https://api.synthflow.ai/v2/calls"
    synthflow_api_key: Optional[str] = None
    synthflow_model_id: Optional[str] = None
    synthflow_timeout_seconds: int = 30
    synthflow_retry_max: int = 3

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model_call_analysis: str = "gpt-4o-mini"
    openai_model_student_summary: str = "gpt-4o-mini"
    openai_model_consent_detector: str = "gpt-4o-mini"
    openai_model_vm_content: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 60
    openai_retry_max: int = 3

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
    new_vm_tier_2_finalizes: Optional[bool] = None
    vm_final_stop_value: int = 3
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

    # ─────────────────────────────────────────────────────────────────────────
    # Field validators
    # ─────────────────────────────────────────────────────────────────────────

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

    def validate_for_openai(self) -> None:
        """Raise ConfigError if OpenAI credentials are missing."""
        if not self.openai_api_key:
            raise ConfigError("OpenAI integration requires OPENAI_API_KEY")

    def validate_for_synthflow(self) -> None:
        """Raise ConfigError if Synthflow credentials are missing."""
        missing = []
        if not self.synthflow_api_key:
            missing.append("SYNTHFLOW_API_KEY")
        if not self.synthflow_model_id:
            missing.append("SYNTHFLOW_MODEL_ID")
        if missing:
            raise ConfigError(
                f"Synthflow integration requires: {', '.join(missing)}"
            )

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
        if self.new_vm_tier_2_finalizes is None:
            missing.append("NEW_VM_TIER_2_FINALIZES")
        if missing:
            raise ConfigError(
                f"New Lead VM tier policy requires: {', '.join(missing)}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
