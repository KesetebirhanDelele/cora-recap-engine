"""
Phase 2 config tests.

Covers:
  - Settings load with only defaults (no .env file, no credentials)
  - Invalid field values raise at load time
  - Mode-flag interpretation: is_shadow_mode, ghl_writes_enabled
  - Idempotency TTL default and validation
  - Context-aware pre-flight validators (validate_for_*) raise ConfigError
    when required fields are absent, pass when present
  - New Lead tier policy validator
  - Cold Lead tier policy defaults are present
  - Consent and summary writeback policy defaults
"""
from __future__ import annotations

import pytest

from app.config.settings import ConfigError, Settings

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_settings(**overrides) -> Settings:
    """Build a Settings from explicit keyword args — never reads .env in these tests."""
    return Settings(
        _env_file=None,
        **overrides,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Boot-time safety: app must load without any credentials
# ─────────────────────────────────────────────────────────────────────────────

def test_settings_load_with_no_env_file():
    s = make_settings()
    assert s.app_name == "cora-recap-engine"


def test_all_external_credentials_optional_by_default():
    s = make_settings()
    assert s.ghl_api_key is None
    assert s.ghl_location_id is None
    assert s.openai_api_key is None
    assert s.synthflow_api_key is None
    assert s.synthflow_model_id is None
    assert s.google_service_account_json is None
    assert s.postgres_password is None
    assert s.redis_password is None


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency TTL
# ─────────────────────────────────────────────────────────────────────────────

def test_idempotency_ttl_defaults_to_90():
    s = make_settings()
    assert s.idempotency_ttl_days == 90


def test_idempotency_ttl_can_be_overridden():
    s = make_settings(idempotency_ttl_days=180)
    assert s.idempotency_ttl_days == 180


def test_idempotency_ttl_zero_raises():
    with pytest.raises(Exception):
        make_settings(idempotency_ttl_days=0)


def test_idempotency_ttl_negative_raises():
    with pytest.raises(Exception):
        make_settings(idempotency_ttl_days=-1)


# ─────────────────────────────────────────────────────────────────────────────
# GHL write-mode field validator
# ─────────────────────────────────────────────────────────────────────────────

def test_ghl_write_mode_shadow_is_valid():
    s = make_settings(ghl_write_mode="shadow")
    assert s.ghl_write_mode == "shadow"


def test_ghl_write_mode_live_is_valid():
    s = make_settings(ghl_write_mode="live")
    assert s.ghl_write_mode == "live"


def test_ghl_write_mode_invalid_raises():
    with pytest.raises(Exception):
        make_settings(ghl_write_mode="read-only")


def test_ghl_write_mode_empty_raises():
    with pytest.raises(Exception):
        make_settings(ghl_write_mode="")


# ─────────────────────────────────────────────────────────────────────────────
# Mode-flag interpretation: is_shadow_mode
# ─────────────────────────────────────────────────────────────────────────────

def test_is_shadow_mode_true_when_write_mode_shadow():
    s = make_settings(ghl_write_mode="shadow", shadow_mode_enabled=False)
    assert s.is_shadow_mode is True


def test_is_shadow_mode_true_when_shadow_mode_enabled_flag_set():
    s = make_settings(ghl_write_mode="live", shadow_mode_enabled=True)
    assert s.is_shadow_mode is True


def test_is_shadow_mode_false_only_when_both_flags_clear():
    s = make_settings(ghl_write_mode="live", shadow_mode_enabled=False)
    assert s.is_shadow_mode is False


# ─────────────────────────────────────────────────────────────────────────────
# Mode-flag interpretation: ghl_writes_enabled
# ─────────────────────────────────────────────────────────────────────────────

def test_ghl_writes_disabled_in_shadow_mode():
    s = make_settings(ghl_write_mode="shadow", ghl_write_shadow_log_only=True)
    assert s.ghl_writes_enabled is False


def test_ghl_writes_disabled_when_shadow_log_only_true_even_if_live():
    s = make_settings(ghl_write_mode="live", ghl_write_shadow_log_only=True)
    assert s.ghl_writes_enabled is False


def test_ghl_writes_enabled_only_when_live_and_not_log_only():
    s = make_settings(ghl_write_mode="live", ghl_write_shadow_log_only=False)
    assert s.ghl_writes_enabled is True


def test_ghl_writes_default_is_disabled():
    s = make_settings()
    assert s.ghl_writes_enabled is False


# ─────────────────────────────────────────────────────────────────────────────
# validate_for_ghl_reads()
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_ghl_reads_raises_without_api_key():
    s = make_settings(ghl_location_id="loc-123")
    with pytest.raises(ConfigError, match="GHL_API_KEY"):
        s.validate_for_ghl_reads()


def test_validate_ghl_reads_raises_without_location_id():
    s = make_settings(ghl_api_key="key-abc")
    with pytest.raises(ConfigError, match="GHL_LOCATION_ID"):
        s.validate_for_ghl_reads()


def test_validate_ghl_reads_raises_when_both_missing():
    s = make_settings()
    with pytest.raises(ConfigError):
        s.validate_for_ghl_reads()


def test_validate_ghl_reads_passes_with_both_present():
    s = make_settings(ghl_api_key="key-abc", ghl_location_id="loc-123")
    s.validate_for_ghl_reads()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# validate_for_ghl_writes()
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_ghl_writes_raises_in_shadow_mode():
    s = make_settings(
        ghl_api_key="key",
        ghl_location_id="loc",
        ghl_write_mode="shadow",
    )
    with pytest.raises(ConfigError, match="GHL writes are disabled"):
        s.validate_for_ghl_writes()


def test_validate_ghl_writes_raises_when_shadow_log_only():
    s = make_settings(
        ghl_api_key="key",
        ghl_location_id="loc",
        ghl_write_mode="live",
        ghl_write_shadow_log_only=True,
    )
    with pytest.raises(ConfigError, match="GHL writes are disabled"):
        s.validate_for_ghl_writes()


def test_validate_ghl_writes_raises_when_missing_read_creds():
    s = make_settings(ghl_write_mode="live", ghl_write_shadow_log_only=False)
    with pytest.raises(ConfigError):
        s.validate_for_ghl_writes()


def test_validate_ghl_writes_passes_when_live_and_credentialed():
    s = make_settings(
        ghl_api_key="key",
        ghl_location_id="loc",
        ghl_write_mode="live",
        ghl_write_shadow_log_only=False,
    )
    s.validate_for_ghl_writes()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# validate_for_openai()
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_openai_raises_without_key():
    s = make_settings()
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        s.validate_for_openai()


def test_validate_openai_passes_with_key():
    s = make_settings(openai_api_key="sk-test")
    s.validate_for_openai()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# validate_for_synthflow()
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_synthflow_raises_without_api_key():
    s = make_settings(synthflow_model_id="model-id")
    with pytest.raises(ConfigError, match="SYNTHFLOW_API_KEY"):
        s.validate_for_synthflow()


def test_validate_synthflow_raises_without_model_id():
    s = make_settings(synthflow_api_key="key")
    with pytest.raises(ConfigError, match="SYNTHFLOW_MODEL_ID"):
        s.validate_for_synthflow()


def test_validate_synthflow_raises_when_both_missing():
    s = make_settings()
    with pytest.raises(ConfigError):
        s.validate_for_synthflow()


def test_validate_synthflow_passes_with_both():
    s = make_settings(synthflow_api_key="key", synthflow_model_id="model-id")
    s.validate_for_synthflow()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# validate_for_sheets_sync()
# ─────────────────────────────────────────────────────────────────────────────

def _sheets_base(**overrides):
    return dict(
        google_service_account_json='{"type":"service_account"}',
        google_sheets_call_log_id="sheet-id-1",
        google_sheets_campaign_data_id="sheet-id-2",
        google_sheets_inbound_tab="Inbound",
        **overrides,
    )


def test_validate_sheets_raises_without_service_account():
    s = make_settings(**{**_sheets_base(), "google_service_account_json": None})
    with pytest.raises(ConfigError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
        s.validate_for_sheets_sync()


def test_validate_sheets_raises_without_call_log_id():
    s = make_settings(**{**_sheets_base(), "google_sheets_call_log_id": None})
    with pytest.raises(ConfigError, match="GOOGLE_SHEETS_CALL_LOG_ID"):
        s.validate_for_sheets_sync()


def test_validate_sheets_raises_without_campaign_data_id():
    s = make_settings(**{**_sheets_base(), "google_sheets_campaign_data_id": None})
    with pytest.raises(ConfigError, match="GOOGLE_SHEETS_CAMPAIGN_DATA_ID"):
        s.validate_for_sheets_sync()


def test_validate_sheets_raises_when_no_tab_configured():
    s = make_settings(**{
        **_sheets_base(),
        "google_sheets_inbound_tab": None,
        "google_sheets_new_leads_tab": None,
        "google_sheets_cold_leads_tab": None,
    })
    with pytest.raises(ConfigError, match="GOOGLE_SHEETS_.*_TAB"):
        s.validate_for_sheets_sync()


def test_validate_sheets_passes_with_minimum_config():
    s = make_settings(**_sheets_base())
    s.validate_for_sheets_sync()  # must not raise


def test_validate_sheets_passes_with_any_single_tab():
    for tab_key in ["google_sheets_new_leads_tab", "google_sheets_cold_leads_tab"]:
        base = {**_sheets_base(), "google_sheets_inbound_tab": None}
        base[tab_key] = "SomeTab"
        s = make_settings(**base)
        s.validate_for_sheets_sync()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# validate_for_new_lead_vm_policy()
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_new_lead_policy_raises_when_delays_unresolved():
    s = make_settings()
    with pytest.raises(ConfigError, match="NEW_VM_TIER"):
        s.validate_for_new_lead_vm_policy()


def test_validate_new_lead_policy_raises_partial():
    s = make_settings(
        new_vm_tier_none_delay_minutes=60,
        new_vm_tier_0_delay_minutes=1440,
    )
    with pytest.raises(ConfigError, match="NEW_VM_TIER"):
        s.validate_for_new_lead_vm_policy()


def test_validate_new_lead_policy_passes_when_all_set():
    s = make_settings(
        new_vm_tier_none_delay_minutes=60,
        new_vm_tier_0_delay_minutes=1440,
        new_vm_tier_1_delay_minutes=1440,
        new_vm_tier_2_finalizes=True,
    )
    s.validate_for_new_lead_vm_policy()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Cold Lead tier policy defaults are present
# ─────────────────────────────────────────────────────────────────────────────

def test_cold_lead_tier_none_delay_is_120_minutes():
    s = make_settings()
    assert s.cold_vm_tier_none_delay_minutes == 120


def test_cold_lead_tier_0_delay_is_2880_minutes():
    s = make_settings()
    assert s.cold_vm_tier_0_delay_minutes == 2880  # 2 days


def test_cold_lead_tier_1_delay_is_2880_minutes():
    s = make_settings()
    assert s.cold_vm_tier_1_delay_minutes == 2880  # 2 days


def test_cold_lead_tier_2_finalizes():
    s = make_settings()
    assert s.cold_vm_tier_2_finalizes is True


def test_vm_final_stop_value_is_3():
    s = make_settings()
    assert s.vm_final_stop_value == 3


# ─────────────────────────────────────────────────────────────────────────────
# Consent and writeback policy defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_writeback_requires_consent_by_default():
    s = make_settings()
    assert s.summary_writeback_requires_consent is True


def test_enable_student_summary_writeback_default():
    s = make_settings()
    assert s.enable_student_summary_writeback is True


def test_task_create_on_completed_call_default():
    s = make_settings()
    assert s.task_create_on_completed_call is True


def test_task_due_date_mode_is_blank():
    s = make_settings()
    assert s.task_due_date_mode == "blank"


# ─────────────────────────────────────────────────────────────────────────────
# New Lead tier delays remain unresolved (Optional) by default
# ─────────────────────────────────────────────────────────────────────────────

def test_new_lead_tier_delays_are_none_by_default():
    s = make_settings()
    assert s.new_vm_tier_none_delay_minutes is None
    assert s.new_vm_tier_0_delay_minutes is None
    assert s.new_vm_tier_1_delay_minutes is None
    assert s.new_vm_tier_2_finalizes is None


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets shadow defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_google_shadow_mode_enabled_by_default():
    s = make_settings()
    assert s.google_shadow_mode_enabled is True


def test_google_sheet_ids_are_none_by_default():
    s = make_settings()
    assert s.google_sheets_call_log_id is None
    assert s.google_sheets_campaign_data_id is None
