"""
Unit tests for support/IPBC GHL-routing added to generate_ghl_call_analysis():

  _load_support_ghl_id()  — resolves the on-shift support assignee via the
                            'support_staff_roster' app_config key.
  _load_ipbc_ghl_id()     — resolves the primary IPBC/payment assignee via
                            the 'ipbc_payment_assistants' app_config key.

Both fall back to a hardcoded default when config is absent/malformed, and
their resolved IDs get substituted into the ghl_call_analysis system prompt
in place of SUPPORT_GHL_ID_HERE / IPBC_GHL_ID_HERE.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.config.settings import Settings
from app.core.ai_message_generator import (
    _IPBC_FALLBACK_GHL_ID,
    _SUPPORT_FALLBACK_GHL_ID,
    _load_ipbc_ghl_id,
    _load_support_ghl_id,
    generate_ghl_call_analysis,
)

# Roster active every hour of every day so these tests never depend on
# wall-clock time — see test_staff_roster.py for the actual shift-window logic.
_ALWAYS_ON_ROSTER = json.dumps([
    {"name": "Always On", "ghl_id": "ALWAYS_ON_ID", "days": [0, 1, 2, 3, 4, 5, 6],
     "shift_start": "00:00", "shift_end": "23:59"},
])


def _settings(**overrides) -> Settings:
    defaults = dict(
        openai_api_key="sk-test",
        ghl_api_key="ghl-test",
        ghl_location_id="loc-test",
        app_env="test",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_oai(response: dict) -> MagicMock:
    client = MagicMock()
    message = MagicMock()
    message.content = json.dumps(response)
    client.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=message)])
    return client


# ── _load_support_ghl_id ─────────────────────────────────────────────────────

def test_load_support_ghl_id_falls_back_when_config_absent():
    with patch("app.core.app_config.get_str", return_value=""):
        assert _load_support_ghl_id(session=MagicMock(), settings=_settings()) == _SUPPORT_FALLBACK_GHL_ID


def test_load_support_ghl_id_falls_back_on_malformed_json():
    with patch("app.core.app_config.get_str", return_value="not json"):
        assert _load_support_ghl_id(session=MagicMock(), settings=_settings()) == _SUPPORT_FALLBACK_GHL_ID


def test_load_support_ghl_id_resolves_from_roster():
    with patch("app.core.app_config.get_str", return_value=_ALWAYS_ON_ROSTER):
        assert _load_support_ghl_id(session=MagicMock(), settings=_settings()) == "ALWAYS_ON_ID"


# ── _load_ipbc_ghl_id ────────────────────────────────────────────────────────

def test_load_ipbc_ghl_id_falls_back_when_config_absent():
    with patch("app.core.app_config.get_str", return_value=""):
        assert _load_ipbc_ghl_id(session=MagicMock(), settings=_settings()) == _IPBC_FALLBACK_GHL_ID


def test_load_ipbc_ghl_id_uses_first_entry_as_primary():
    raw = json.dumps([
        {"name": "New IPBC Person", "ghl_id": "NEW_IPBC_ID"},
        {"name": "Taiwo", "ghl_id": "93bhNRgb5pzSoHmaSimH"},
    ])
    with patch("app.core.app_config.get_str", return_value=raw):
        assert _load_ipbc_ghl_id(session=MagicMock(), settings=_settings()) == "NEW_IPBC_ID"


# ── generate_ghl_call_analysis: prompt substitution ─────────────────────────

def test_prompt_placeholders_substituted_with_resolved_ids():
    oai = _mock_oai({
        "task_title": "Follow-Up",
        "task_description": "desc",
        "assign_to": "ALWAYS_ON_ID",
        "is_lead_classification": False,
        "lead_classification": "support_call",
        "create_task": "yes",
        "outbound_call_details": "",
        "call_detailed_summary": "",
        "ai_campaign": "No",
        "call_start_time_formatted": "",
        "task_due_date": "",
    })

    with patch("app.core.app_config.get_str", return_value=_ALWAYS_ON_ROSTER):
        generate_ghl_call_analysis(
            transcript="I need help logging into my account.",
            call_start_time_ms=1700000000000,
            duration_seconds=60,
            contact_phone="+15551234567",
            settings=_settings(),
            session=MagicMock(),
            _client=oai,
        )

    sent_messages = oai.chat.completions.create.call_args.kwargs["messages"]
    system_content = sent_messages[0]["content"]
    assert "SUPPORT_GHL_ID_HERE" not in system_content
    assert "ADMISSIONS_GHL_ID_HERE" not in system_content
    assert "IPBC_GHL_ID_HERE" not in system_content
    assert "ALWAYS_ON_ID" in system_content  # support + admissions + ipbc all resolve to it here
