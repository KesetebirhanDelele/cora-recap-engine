"""
Unit tests for app/services/alerting.py — spec/30 additions only
(new_exception per-incident email, sales_queue_urgent routed friendly email,
and the ALERT_EMAIL_TO multi-recipient split fix). The pre-existing
evaluators in this module (queue_lag_exceeded, exception_spike, etc.) have
no prior test coverage and are out of scope here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.alerting import (
    _evaluate_new_exceptions,
    _evaluate_sales_queue_urgent,
    _route_sales_queue_recipients,
    _sales_queue_routing_reason,
    _send_alert_email,
    _send_sales_queue_urgent_email,
)


def _make_settings(**overrides) -> SimpleNamespace:
    base = dict(
        smtp_enabled=True,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_use_tls=True,
        smtp_username="user",
        smtp_password="pass",
        alert_email_from="cora@colaberry.com",
        alert_email_to="kesetebeirhan@gmail.com",
        alert_email_rose="roselen@colaberry.com",
        alert_email_taiwo="taiwo@colaberry.com",
        alert_sales_queue_enabled=True,  # tests exercise the enabled path by default
        frontend_url="https://cora.colaberry.com",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── _route_sales_queue_recipients ───────────────────────────────────────────

def test_route_admissions_keyword_goes_to_rose():
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(
        settings, "I wanted to ask about the admissions requirements for the program."
    )
    assert recipients == ["roselen@colaberry.com"]


def test_route_payment_keyword_goes_to_taiwo():
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(
        settings, "I have a question about my IPBC payment and the invoice balance."
    )
    assert recipients == ["taiwo@colaberry.com"]


def test_route_both_keywords_goes_to_both():
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(
        settings, "I want to apply to the program but also ask about the tuition payment plan."
    )
    assert set(recipients) == {"roselen@colaberry.com", "taiwo@colaberry.com"}


def test_route_named_person_overrides_topic():
    settings = _make_settings()
    # Payment keyword present, but caller explicitly asked for Rose — name wins.
    recipients = _route_sales_queue_recipients(
        settings, "Can I please talk to Rose about my payment?"
    )
    assert recipients == ["roselen@colaberry.com"]


def test_route_both_named_goes_to_both():
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(
        settings, "I'd like to speak with either Rose or Taiwo, whoever is available."
    )
    assert set(recipients) == {"roselen@colaberry.com", "taiwo@colaberry.com"}


def test_route_name_substring_does_not_false_match():
    """'Rosetta' should not match 'Rose' — word-boundary regex required."""
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(
        settings, "My name is Rosetta and I have a general question."
    )
    assert set(recipients) == {"roselen@colaberry.com", "taiwo@colaberry.com"}


def test_route_no_signal_defaults_to_both():
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(settings, "Hello, just checking in.")
    assert set(recipients) == {"roselen@colaberry.com", "taiwo@colaberry.com"}


def test_route_handles_none_transcript():
    settings = _make_settings()
    recipients = _route_sales_queue_recipients(settings, None)
    assert set(recipients) == {"roselen@colaberry.com", "taiwo@colaberry.com"}


# ── _sales_queue_routing_reason ──────────────────────────────────────────────

def test_routing_reason_names_the_person_when_asked_by_name():
    assert "Rose" in _sales_queue_routing_reason("Can I talk to Rose please?")


def test_routing_reason_describes_admissions_topic():
    reason = _sales_queue_routing_reason("I want to ask about the admissions requirements.")
    assert "admissions" in reason


def test_routing_reason_describes_payment_topic():
    reason = _sales_queue_routing_reason("question about my IPBC invoice")
    assert "payment" in reason


def test_routing_reason_describes_unclassified_fallback():
    reason = _sales_queue_routing_reason("Hello, just checking in.")
    assert "Rose" in reason and "Taiwo" in reason


# ── _send_alert_email multi-recipient fix ───────────────────────────────────

@patch("app.services.alerting.smtplib.SMTP")
def test_send_alert_email_splits_comma_separated_recipients(mock_smtp_cls):
    settings = _make_settings(alert_email_to="a@x.com, b@y.com")
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    from datetime import datetime, timezone
    _send_alert_email(
        settings=settings, alert_id="a1", alert_type="queue_lag_exceeded",
        severity="critical", message="test", now=datetime.now(tz=timezone.utc),
        is_resolution=False,
    )

    assert mock_server.sendmail.call_count == 1
    args = mock_server.sendmail.call_args
    to_addrs = args[0][1]
    assert to_addrs == ["a@x.com", "b@y.com"]


@patch("app.services.alerting.smtplib.SMTP")
def test_send_alert_email_to_override_bypasses_default_recipient(mock_smtp_cls):
    settings = _make_settings(alert_email_to="kesetebeirhan@gmail.com")
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    from datetime import datetime, timezone
    _send_alert_email(
        settings=settings, alert_id="a1", alert_type="sales_queue_urgent",
        severity="warning", message="test", now=datetime.now(tz=timezone.utc),
        is_resolution=False, to_override=["roselen@colaberry.com"],
    )

    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["roselen@colaberry.com"]


@patch("app.services.alerting.smtplib.SMTP")
def test_send_alert_email_skips_when_smtp_disabled(mock_smtp_cls):
    settings = _make_settings(smtp_enabled=False)
    from datetime import datetime, timezone
    _send_alert_email(
        settings=settings, alert_id="a1", alert_type="x", severity="warning",
        message="test", now=datetime.now(tz=timezone.utc), is_resolution=False,
    )
    mock_smtp_cls.assert_not_called()


# ── _send_sales_queue_urgent_email friendly template ────────────────────────

@patch("app.services.alerting.smtplib.SMTP")
def test_sales_queue_urgent_email_is_friendly_not_generic(mock_smtp_cls):
    """No 'Alert ID' / 'Cora Alert' / 'log in to the dashboard' system-alert
    framing — Rose and Taiwo aren't Cora operators."""
    settings = _make_settings()
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    _send_sales_queue_urgent_email(
        settings=settings, to_addrs=["roselen@colaberry.com"], cc_addrs=None,
        lead_name="Jane Doe", phone="+15551234567", intent="callback_with_time",
        call_time_str="2026-09-11T00:09:05+00:00",
        transcript_excerpt="I'd like to know about the admissions requirements.",
        routing_reason="the call sounded admissions-related",
        callback_note="Cora already has a follow-up call scheduled for 2026-09-12T15:00:00 UTC.",
    )

    sent = mock_server.sendmail.call_args[0][2]  # raw message string
    assert "Alert ID" not in sent
    assert "Cora Alert" not in sent
    assert "log in to the dashboard" not in sent.lower()
    assert "Jane Doe needs a follow-up" in sent
    assert "+15551234567" in sent
    assert "admissions requirements" in sent
    assert "already has a follow-up call scheduled" in sent
    assert "Check this lead's GHL account" in sent
    assert "http" not in sent.lower()  # no dashboard link — GHL lookup instead


@patch("app.services.alerting.smtplib.SMTP")
def test_sales_queue_urgent_email_ccs_when_cc_addrs_given(mock_smtp_cls):
    """The evaluator currently always passes cc_addrs=None (Kes asked not to
    be CC'd, 2026-09-11) — this test covers the CC mechanism itself, which
    stays available for if that preference changes again."""
    settings = _make_settings()
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    _send_sales_queue_urgent_email(
        settings=settings, to_addrs=["roselen@colaberry.com"],
        cc_addrs=["kesetebeirhan@gmail.com"],
        lead_name="Jane Doe", phone="+15551234567", intent="callback_with_time",
        call_time_str="2026-09-11T00:09:05+00:00",
        transcript_excerpt="admissions question",
        routing_reason="the call sounded admissions-related",
        callback_note="",
    )

    envelope_addrs = mock_server.sendmail.call_args[0][1]
    assert set(envelope_addrs) == {"roselen@colaberry.com", "kesetebeirhan@gmail.com"}
    sent = mock_server.sendmail.call_args[0][2]
    assert "Cc: kesetebeirhan@gmail.com" in sent


@patch("app.services.alerting.smtplib.SMTP")
def test_sales_queue_urgent_email_falls_back_to_phone_when_no_name(mock_smtp_cls):
    settings = _make_settings()
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    _send_sales_queue_urgent_email(
        settings=settings, to_addrs=["taiwo@colaberry.com"], cc_addrs=None,
        lead_name="", phone="+15551234567", intent="callback_request",
        call_time_str="2026-09-11T00:09:05+00:00",
        transcript_excerpt="question about payment",
        routing_reason="the call sounded payment/IPBC-related",
        callback_note="",
    )

    sent = mock_server.sendmail.call_args[0][2]
    # Subject falls back to phone when there's no lead name; body falls back
    # to the generic "A lead" (phone still appears in the Phone: line).
    assert "Subject: Urgent lead needs follow-up - +15551234567" in sent
    assert "A lead needs a follow-up from you." in sent
    assert "Phone: +15551234567" in sent


# ── _evaluate_new_exceptions ─────────────────────────────────────────────────

def _mock_session_dispatch(fetchall_by_query=None, fetchone_by_query=None):
    """
    Build a MagicMock session whose .execute(text(sql), params) inspects the
    SQL text for a caller-supplied substring key and returns the matching
    canned result. Falls back to an empty MagicMock for unmatched queries.
    """
    fetchall_by_query = fetchall_by_query or {}
    fetchone_by_query = fetchone_by_query or {}

    def _execute(clause, params=None):
        sql = str(clause)
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        for key, rows in fetchall_by_query.items():
            if key in sql:
                result.fetchall.return_value = rows
        for key, row in fetchone_by_query.items():
            if key in sql:
                result.fetchone.return_value = row
        return result

    session = MagicMock()
    session.execute.side_effect = _execute
    return session


@patch("app.services.alerting._send_alert_email")
def test_new_exceptions_first_run_seeds_without_emailing(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(
        fetchone_by_query={"exception_notified:%": None},  # ledger empty -> first run
        fetchall_by_query={
            "FROM exceptions": [
                ("exc-1", "call_analysis_failed", "critical", "call", "call-1", {"error": "x"}, None),
            ]
        },
    )
    _evaluate_new_exceptions(session, settings, datetime.now(tz=timezone.utc))

    session.add.assert_called_once()
    mock_send.assert_not_called()


@patch("app.services.alerting._send_alert_email")
def test_new_exceptions_subsequent_run_emails_once(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(
        fetchone_by_query={"exception_notified:%": (1,)},  # ledger non-empty -> not first run
        fetchall_by_query={
            "FROM exceptions": [
                ("exc-2", "send_sms_failed", "warning", "lead", "+15551234567", {"error": "timeout"}, None),
            ]
        },
    )
    _evaluate_new_exceptions(session, settings, datetime.now(tz=timezone.utc))

    session.add.assert_called_once()
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["alert_type"] == "new_exception"
    assert kwargs["alert_id"] == "exc-2"
    assert kwargs["severity"] == "warning"
    assert "send_sms_failed" in kwargs["message"]


@patch("app.services.alerting._send_alert_email")
def test_new_exceptions_no_open_rows_no_email(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(
        fetchone_by_query={"exception_notified:%": (1,)},
        fetchall_by_query={"FROM exceptions": []},
    )
    _evaluate_new_exceptions(session, settings, datetime.now(tz=timezone.utc))

    session.add.assert_not_called()
    mock_send.assert_not_called()


# ── _evaluate_sales_queue_urgent ─────────────────────────────────────────────
# Row shape: contact_id, intent, transcript, call_time, sales_outcome,
#            next_callback_at, next_callback_reason, phone, lead_name

@patch("app.services.alerting._send_sales_queue_urgent_email")
def test_sales_queue_urgent_new_lead_emails_and_creates_alert(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(
        fetchall_by_query={
            "FROM call_events": [
                ("+15082722326", "callback_with_time", "I need a callback about payment",
                 datetime.now(tz=timezone.utc), None, None, None,
                 "+15082722326", None),
            ]
        },
        fetchone_by_query={"FROM alert_events": None},  # no existing alert row
    )
    _evaluate_sales_queue_urgent(session, settings, datetime.now(tz=timezone.utc))

    session.add.assert_called_once()
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_addrs"] == ["taiwo@colaberry.com"]
    assert kwargs["cc_addrs"] is None  # Kes asked not to be CC'd (2026-09-11)
    assert kwargs["phone"] == "+15082722326"
    assert kwargs["callback_note"] == ""


@patch("app.services.alerting._send_sales_queue_urgent_email")
def test_sales_queue_urgent_includes_already_booked_callback_time(mock_send):
    """When Cora already scheduled a launch_outbound_call for this contact
    (e.g. callback_with_time extracted a specific promised time), the email
    must say so, and when — so Rose/Taiwo don't double-book."""
    from datetime import datetime, timezone
    settings = _make_settings()
    booked_at = datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
    session = _mock_session_dispatch(
        fetchall_by_query={
            "FROM call_events": [
                ("+15082722326", "callback_with_time", "call me back at 3pm about admissions",
                 datetime.now(tz=timezone.utc), None, booked_at, "callback_with_time",
                 "+15082722326", "Jane Doe"),
            ]
        },
        fetchone_by_query={"FROM alert_events": None},
    )
    _evaluate_sales_queue_urgent(session, settings, datetime.now(tz=timezone.utc))

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["lead_name"] == "Jane Doe"
    assert "already has a follow-up call scheduled" in kwargs["callback_note"]
    assert "2026-09-12T15:00:00" in kwargs["callback_note"]
    assert "callback_with_time" in kwargs["callback_note"]


@patch("app.services.alerting._send_sales_queue_urgent_email")
def test_sales_queue_urgent_already_active_skips_duplicate_email(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(
        fetchall_by_query={
            "FROM call_events": [
                ("+15082722326", "callback_with_time", "payment question",
                 datetime.now(tz=timezone.utc), None, None, None,
                 "+15082722326", None),
            ]
        },
        fetchone_by_query={"FROM alert_events": ("existing-id", "active")},
    )
    _evaluate_sales_queue_urgent(session, settings, datetime.now(tz=timezone.utc))

    session.add.assert_not_called()
    mock_send.assert_not_called()


@patch("app.services.alerting._send_sales_queue_urgent_email")
def test_sales_queue_urgent_resolved_by_rep_closes_silently(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(
        fetchall_by_query={
            "FROM call_events": [
                ("+15082722326", "callback_with_time", "payment question",
                 datetime.now(tz=timezone.utc), "booked", None, None,
                 "+15082722326", None),  # sales_outcome set -> resolved
            ]
        },
        fetchone_by_query={"FROM alert_events": ("existing-id", "active")},
    )
    _evaluate_sales_queue_urgent(session, settings, datetime.now(tz=timezone.utc))

    mock_send.assert_not_called()
    session.add.assert_not_called()
    # Should have issued an UPDATE to resolve the existing active row.
    update_calls = [c for c in session.execute.call_args_list if "UPDATE alert_events" in str(c.args[0])]
    assert len(update_calls) == 1


@patch("app.services.alerting._send_sales_queue_urgent_email")
def test_sales_queue_urgent_no_qualifying_leads_no_op(mock_send):
    from datetime import datetime, timezone
    settings = _make_settings()
    session = _mock_session_dispatch(fetchall_by_query={"FROM call_events": []})
    _evaluate_sales_queue_urgent(session, settings, datetime.now(tz=timezone.utc))

    session.add.assert_not_called()
    mock_send.assert_not_called()


@patch("app.services.alerting._send_sales_queue_urgent_email")
def test_sales_queue_urgent_disabled_is_a_no_op(mock_send):
    """alert_sales_queue_enabled=False gates this whole evaluator off."""
    from datetime import datetime, timezone
    settings = _make_settings(alert_sales_queue_enabled=False)
    session = _mock_session_dispatch(
        fetchall_by_query={
            "FROM call_events": [
                ("+15082722326", "callback_with_time", "payment question",
                 datetime.now(tz=timezone.utc), None, None, None,
                 "+15082722326", None),
            ]
        },
    )
    _evaluate_sales_queue_urgent(session, settings, datetime.now(tz=timezone.utc))

    session.execute.assert_not_called()
    session.add.assert_not_called()
    mock_send.assert_not_called()
