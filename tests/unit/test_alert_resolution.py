"""Resolved report for a hand-resolved exception (Ali 2026-10-08)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.core.alert_resolution import build_resolution_email

NOW = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
EXC = {"id": "e1", "type": "outbound_launch_failed", "severity": "critical", "entity_type": "call",
       "entity_id": "+16824081767", "context": {"error": "[Errno 101] Network is unreachable", "job_id": "j1", "traceback": "x"},
       "created_at": datetime(2026, 10, 8, 15, 36, tzinfo=timezone.utc)}


def test_report_names_the_error_the_fix_and_who_closed_it():
    actions = [{"action": "retry_now", "operator": "kes", "at": NOW, "context": {"new_job_id": "5d9987fc-aaaa"},
                "job_status": "completed"}]
    subject, body = build_resolution_email(EXC, actions, "kes", "network blip, call re-run", NOW)
    assert subject == "[RESOLVED] Cora Alert: outbound_launch_failed"
    assert "Network is unreachable" in body and "+16824081767" in body
    assert "re-ran the failed job immediately; the new job 5d9987fc completed" in body
    assert "kes marked it resolved: \"network blip, call re-run\"" in body
    assert "traceback" not in body


def test_report_without_a_retry_or_note_still_says_how():
    _, body = build_resolution_email(EXC, [], "kes", "operator resolved", NOW)
    assert "HOW IT WAS RESOLVED" in body and "kes marked it resolved." in body


def test_resolved_report_goes_to_the_alert_recipients_with_cc_and_never_raises():
    from app.services import alerting

    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("e1", "outbound_launch_failed", "critical", "call", "+1", {"error": "x"}, NOW)
    session.execute.return_value.fetchall.return_value = []
    settings = MagicMock(alert_email_to="kes@x.com", alert_email_cc="ali@x.com")
    with patch.object(alerting, "_smtp_send") as send:
        alerting.send_exception_resolved_email(session, settings, "e1", "kes", "done")
    args, kw = send.call_args
    assert args[1] == ["kes@x.com"] and kw["cc_addrs"] == ["ali@x.com"] and args[2].startswith("[RESOLVED]")
    assert kw["extra_headers"]["X-Cora-Alert"] == "system" and kw["extra_headers"]["X-Cora-Alert-State"] == "resolved"
    session.execute.side_effect = RuntimeError("db down")
    alerting.send_exception_resolved_email(session, settings, "e1", "kes", "done")     # must not raise


def test_retry_payload_carries_the_failed_jobs_own_payload_not_just_the_error():
    from app.services.dashboard import _retry_payload

    original = MagicMock(payload_json={"contact_id": "+16824081767", "phone_number": "+16824081767", "campaign_name": "Cold Lead"})
    session = MagicMock()
    session.get.return_value = original
    ctx = {"error": "[Errno 101] Network is unreachable", "job_id": "j1"}
    p = _retry_payload(session, ctx)
    assert p["phone_number"] == "+16824081767" and p["campaign_name"] == "Cold Lead" and p["error"] == ctx["error"]
    session.get.return_value = None                                   # original job row gone: context only, as before
    assert _retry_payload(session, ctx) == ctx
    assert _retry_payload(session, {"error": "x"}) == {"error": "x"}
