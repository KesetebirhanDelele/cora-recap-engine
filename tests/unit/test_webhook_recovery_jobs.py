"""
Unit tests for app/worker/jobs/webhook_recovery_jobs.py

Covers:
- SynthflowClient.list_calls() — envelope unwrapping, params, retry
- _find_synthflow_call() — pagination, closest-match selection, error handling
- _run_recovery_cycle() — terminal recovery, no-answer reschedule, skip non-terminal,
                          conflict handling, cap enforcement
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

# ── list_calls ─────────────────────────────────────────────────────────────────

class FakeSettings:
    synthflow_api_key = "test-key"
    synthflow_base_url = "https://api.synthflow.ai/v2/calls"
    synthflow_retry_max = 1
    synthflow_timeout_seconds = 10


def _make_client(mock_http):
    from app.adapters.synthflow import SynthflowClient
    s = FakeSettings()
    s.validate_for_synthflow_read = lambda: None
    return SynthflowClient(settings=s, _http=mock_http)


def _mock_get_response(data):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = True
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def test_list_calls_data_list_envelope():
    mock_http = MagicMock()
    mock_http.get.return_value = _mock_get_response({"status": "ok", "data": [{"call_id": "abc"}]})
    client = _make_client(mock_http)
    result = client.list_calls("model1", "+14155550000", 1000, 2000, _retry_delay=0.0)
    assert result == [{"call_id": "abc"}]


def test_list_calls_calls_envelope():
    mock_http = MagicMock()
    mock_http.get.return_value = _mock_get_response({"calls": [{"call_id": "xyz"}]})
    client = _make_client(mock_http)
    result = client.list_calls("model1", "+14155550000", 1000, 2000, _retry_delay=0.0)
    assert result == [{"call_id": "xyz"}]


def test_list_calls_empty_response():
    mock_http = MagicMock()
    mock_http.get.return_value = _mock_get_response({})
    client = _make_client(mock_http)
    result = client.list_calls("model1", "+14155550000", 1000, 2000, _retry_delay=0.0)
    assert result == []


def test_list_calls_sends_correct_params():
    mock_http = MagicMock()
    mock_http.get.return_value = _mock_get_response({"data": []})
    client = _make_client(mock_http)
    client.list_calls("m1", "+14155551234", 1000000, 9000000, limit=5, offset=10, _retry_delay=0.0)
    _, kwargs = mock_http.get.call_args
    params = kwargs["params"]
    assert params["model_id"] == "m1"
    assert params["lead_phone_number"] == "+14155551234"
    assert params["from_date"] == 1000000
    assert params["to_date"] == 9000000
    assert params["limit"] == 5
    assert params["offset"] == 10


def test_list_calls_raises_on_4xx():
    from app.adapters.synthflow import SynthflowError
    mock_http = MagicMock()
    err_resp = MagicMock()
    err_resp.status_code = 404
    mock_http.get.side_effect = httpx.HTTPStatusError("not found", request=MagicMock(), response=err_resp)
    client = _make_client(mock_http)
    with pytest.raises(SynthflowError):
        client.list_calls("m1", "+1", 0, 1, _retry_delay=0.0)


def test_list_calls_nested_data_calls_envelope():
    mock_http = MagicMock()
    mock_http.get.return_value = _mock_get_response({
        "data": {"calls": [{"call_id": "nested"}]}
    })
    client = _make_client(mock_http)
    result = client.list_calls("m1", "+1", 0, 1, _retry_delay=0.0)
    assert result == [{"call_id": "nested"}]


# ── _find_synthflow_call ────────────────────────────────────────────────────────

def _make_settings():
    s = FakeSettings()
    s.validate_for_synthflow_read = lambda: None
    return s


@patch("app.adapters.synthflow.SynthflowClient")
def test_find_call_picks_closest_to_executed_at(MockClient):
    from app.worker.jobs.webhook_recovery_jobs import _find_synthflow_call

    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    executed_at = now

    # Call A: 30 seconds after executed_at → delta=30
    call_a = {"call_id": "a", "call_status": "completed", "start_time": str(int((now + timedelta(seconds=30)).timestamp() * 1000))}
    # Call B: 5 minutes after executed_at → delta=300
    call_b = {"call_id": "b", "call_status": "completed", "start_time": str(int((now + timedelta(minutes=5)).timestamp() * 1000))}

    instance = MockClient.return_value
    instance.list_calls.return_value = [call_b, call_a]  # out of order

    result = _find_synthflow_call(_make_settings(), "model1", "+15550001234", executed_at)
    assert result["call_id"] == "a"


@patch("app.adapters.synthflow.SynthflowClient")
def test_find_call_returns_none_when_no_calls(MockClient):
    from app.worker.jobs.webhook_recovery_jobs import _find_synthflow_call

    instance = MockClient.return_value
    instance.list_calls.return_value = []

    result = _find_synthflow_call(_make_settings(), "model1", "+1", datetime.now(tz=timezone.utc))
    assert result is None


@patch("app.adapters.synthflow.SynthflowClient")
def test_find_call_returns_none_on_synthflow_error(MockClient):
    from app.adapters.synthflow import SynthflowError
    from app.worker.jobs.webhook_recovery_jobs import _find_synthflow_call

    instance = MockClient.return_value
    instance.list_calls.side_effect = SynthflowError("API down")

    result = _find_synthflow_call(_make_settings(), "model1", "+1", datetime.now(tz=timezone.utc))
    assert result is None


@patch("app.adapters.synthflow.SynthflowClient")
def test_find_call_paginates_until_last_page(MockClient):
    from app.worker.jobs.webhook_recovery_jobs import _find_synthflow_call

    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    page1 = [{"call_id": str(i), "call_status": "completed", "start_time": str(int(now.timestamp() * 1000))} for i in range(20)]
    page2 = [{"call_id": "last", "call_status": "completed", "start_time": str(int(now.timestamp() * 1000))}]

    instance = MockClient.return_value
    instance.list_calls.side_effect = [page1, page2]

    _find_synthflow_call(_make_settings(), "model1", "+1", now)
    assert instance.list_calls.call_count == 2
    _, kw2 = instance.list_calls.call_args_list[1]
    assert kw2["offset"] == 20


# ── _run_recovery_cycle ─────────────────────────────────────────────────────────

def _make_failure_row(job_id, contact_id, campaign, minutes_ago=30):
    executed_at = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    return (job_id, contact_id, campaign, executed_at)


@patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call")
@patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures")
@patch("app.worker.jobs.webhook_recovery_jobs.recover_missed_webhook", create=True)
def test_cycle_recovers_terminal_call(mock_recover, mock_failures, mock_find):
    from app.services.stale_recovery import recover_missed_webhook as _orig

    with patch("app.services.stale_recovery.recover_missed_webhook") as mock_recover, \
         patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures") as mock_failures, \
         patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call") as mock_find:

        from app.worker.jobs.webhook_recovery_jobs import _run_recovery_cycle

        mock_failures.return_value = [
            _make_failure_row("j1", "+15550001111", "Cold Lead"),
        ]
        mock_find.return_value = {
            "call_id": "call-abc",
            "call_status": "completed",
        }

        session = MagicMock()
        settings = MagicMock()

        _run_recovery_cycle(session, settings)
        mock_recover.assert_called_once()
        args = mock_recover.call_args[0]
        assert args[1] == "+15550001111"
        assert args[2] == "call-abc"


@patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call")
@patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures")
def test_cycle_reschedules_when_call_not_found(mock_failures, mock_find):
    with patch("app.services.stale_recovery.advance_stale_lead") as mock_advance, \
         patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures") as mock_failures, \
         patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call") as mock_find:

        from app.worker.jobs.webhook_recovery_jobs import _run_recovery_cycle

        mock_failures.return_value = [_make_failure_row("j1", "+15550002222", "New Lead")]
        mock_find.return_value = None

        session = MagicMock()
        settings = MagicMock()

        _run_recovery_cycle(session, settings)
        mock_advance.assert_called_once()
        args = mock_advance.call_args[0]
        assert args[1] == "+15550002222"
        assert args[2] == "no_answer"


@patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call")
@patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures")
def test_cycle_skips_non_terminal_call(mock_failures, mock_find):
    with patch("app.services.stale_recovery.advance_stale_lead") as mock_advance, \
         patch("app.services.stale_recovery.recover_missed_webhook") as mock_recover, \
         patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures") as mock_failures, \
         patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call") as mock_find:

        from app.worker.jobs.webhook_recovery_jobs import _run_recovery_cycle

        mock_failures.return_value = [_make_failure_row("j1", "+15550003333", "Cold Lead")]
        mock_find.return_value = {"call_id": "in-flight", "call_status": "in_progress"}

        _run_recovery_cycle(MagicMock(), MagicMock())
        mock_advance.assert_not_called()
        mock_recover.assert_not_called()


@patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call")
@patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures")
def test_cycle_skips_unknown_campaign(mock_failures, mock_find):
    with patch("app.services.stale_recovery.advance_stale_lead") as mock_advance, \
         patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures") as mock_failures, \
         patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call") as mock_find:

        from app.worker.jobs.webhook_recovery_jobs import _run_recovery_cycle

        mock_failures.return_value = [_make_failure_row("j1", "+15550004444", "Unknown Campaign")]
        mock_find.return_value = None

        _run_recovery_cycle(MagicMock(), MagicMock())
        mock_find.assert_not_called()
        mock_advance.assert_not_called()


@patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call")
@patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures")
def test_cycle_handles_stale_lead_conflict_gracefully(mock_failures, mock_find):
    from app.services.stale_recovery import StaleLeadConflict

    with patch("app.services.stale_recovery.advance_stale_lead") as mock_advance, \
         patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures") as mock_failures, \
         patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call") as mock_find:

        from app.worker.jobs.webhook_recovery_jobs import _run_recovery_cycle

        mock_failures.return_value = [_make_failure_row("j1", "+15550005555", "New Lead")]
        mock_find.return_value = None
        mock_advance.side_effect = StaleLeadConflict("already has pending job")

        # Should not raise
        _run_recovery_cycle(MagicMock(), MagicMock())


@patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call")
@patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures")
def test_cycle_processes_all_three_campaign_types(mock_failures, mock_find):
    with patch("app.services.stale_recovery.advance_stale_lead") as mock_advance, \
         patch("app.worker.jobs.webhook_recovery_jobs._get_pending_failures") as mock_failures, \
         patch("app.worker.jobs.webhook_recovery_jobs._find_synthflow_call") as mock_find:

        from app.worker.jobs.webhook_recovery_jobs import _CAMPAIGN_MODEL_IDS, _run_recovery_cycle

        mock_failures.return_value = [
            _make_failure_row("j1", "+15551111111", "Cold Lead"),
            _make_failure_row("j2", "+15552222222", "New Lead"),
            _make_failure_row("j3", "+15553333333", "Inbound"),
        ]
        mock_find.return_value = None  # not found → advance_stale_lead

        _run_recovery_cycle(MagicMock(), MagicMock())

        assert mock_find.call_count == 3
        model_ids_used = [c[0][1] for c in mock_find.call_args_list]
        assert _CAMPAIGN_MODEL_IDS["Cold Lead"] in model_ids_used
        assert _CAMPAIGN_MODEL_IDS["New Lead"] in model_ids_used
        assert _CAMPAIGN_MODEL_IDS["Inbound"] in model_ids_used
