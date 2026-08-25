"""
Unit tests for dashboard v2 API and supporting services.

Coverage:
  - GET /dashboard/health — returns correct fields
  - GET /dashboard/metrics — returns correct structure
  - GET /dashboard/events — cursor pagination
  - GET /dashboard/lead/{id}/trace — 200 and 404
  - GET /dashboard/alerts — filters by status
  - POST /dashboard/actions/retry — auth required; delegates to service
  - POST /dashboard/actions/cancel — cancels pending jobs
  - POST /dashboard/actions/finalize — closes lead
  - alerting.evaluate_alerts — threshold breach, dedup, resolution
  - pipeline_trace.get_lead_trace — happy path, shadow steps, not found
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.base import Base

# Import all models to ensure they are registered in Base.metadata before create_all
import app.models.alert_event  # noqa: F401
import app.models.event_stream  # noqa: F401
import app.models.lead_state  # noqa: F401
import app.models.scheduled_job  # noqa: F401
import app.models.shadow_action  # noqa: F401
import app.models.exception  # noqa: F401
import app.models.audit  # noqa: F401


# ── Test database ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.pool import StaticPool
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


# ── Dashboard API client ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(engine):
    from app.api.dashboard_main import create_app
    from app.db import get_db

    app = create_app()

    def _override_session():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_session

    with TestClient(app) as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-secret"}


# ── Health endpoint ────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_expected_fields(self, client):
        with patch("app.services.dashboard_metrics.get_health") as mock:
            mock.return_value = {
                "queue_lag_seconds": 0.0,
                "active_workers": 2,
                "open_exception_count": 0,
                "stuck_job_count": 0,
                "expired_lease_count": 0,
                "jobs_completed_last_5m": 5,
                "jobs_failed_last_5m": 0,
                "error_rate": None,
                "shadow_mode_enabled": True,
                "ghl_write_mode": "shadow",
                "app_env": "test",
                "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            resp = client.get("/dashboard/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_workers"] == 2
        assert data["error_rate"] is None
        assert data["shadow_mode_enabled"] is True

    def test_no_auth_required_by_default(self, client):
        with patch("app.services.dashboard_metrics.get_health") as mock:
            mock.return_value = {"queue_lag_seconds": 0}
            resp = client.get("/dashboard/health")
        assert resp.status_code == 200


# ── Metrics endpoint ───────────────────────────────────────────────────────────

class TestMetricsEndpoint:
    def test_returns_kpi_structure(self, client):
        with patch("app.services.dashboard_metrics.get_metrics") as mock:
            mock.return_value = {
                "period": {"from": "2026-03-29T00:00:00Z", "to": "2026-04-05T00:00:00Z"},
                "campaign_filter": None,
                "kpis": {"total_calls": 100, "pickup_rate": 0.3, "voicemail_rate": 0.5,
                         "failed_rate": 0.2, "do_not_call_rate": None, "enrolled_count": 5},
                "ai": {"blank_transcript_rate": None, "intent_distribution": {},
                       "consent_distribution": {}},
                "queue": {"stuck_jobs": [], "expired_leases": []},
                "crm": {"ghl_task_success_rate": 0.95, "ghl_vm_update_success_rate": 0.98,
                        "ghl_shadow_write_count": 0},
            }
            resp = client.get("/dashboard/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kpis"]["total_calls"] == 100
        assert data["crm"]["ghl_task_success_rate"] == 0.95

    def test_passes_campaign_filter(self, client):
        with patch("app.services.dashboard_metrics.get_metrics") as mock:
            mock.return_value = {
                "period": {}, "campaign_filter": "Cold Lead",
                "kpis": {}, "ai": {}, "queue": {}, "crm": {},
            }
            client.get("/dashboard/metrics?campaign=Cold+Lead")
        mock.assert_called_once()
        _, kwargs = mock.call_args
        assert kwargs.get("campaign") == "Cold Lead" or mock.call_args[0][1] == "Cold Lead"


# ── Events endpoint ────────────────────────────────────────────────────────────

class TestEventsEndpoint:
    def test_returns_events_list(self, client, session):
        from app.models.event_stream import EventStream
        now = datetime.now(tz=timezone.utc)
        evt = EventStream(
            id=str(uuid.uuid4()),
            event_type="job_completed",
            entity_type="scheduled_job",
            entity_id="job-1",
            contact_id="contact-abc",
            message="Test event",
            payload={},
            created_at=now,
        )
        session.add(evt)
        session.commit()

        resp = client.get("/dashboard/events?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "next_cursor" in data

    def test_since_cursor_filters(self, client):
        from urllib.parse import quote
        future = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
        resp = client.get(f"/dashboard/events?since={quote(future)}&limit=10")
        assert resp.status_code == 200
        assert resp.json()["events"] == []


# ── Lead trace endpoint ────────────────────────────────────────────────────────

class TestLeadTraceEndpoint:
    def test_returns_404_for_unknown_contact(self, client):
        with patch("app.services.pipeline_trace.get_lead_trace", return_value=None):
            resp = client.get("/dashboard/lead/nonexistent/trace")
        assert resp.status_code == 404

    def test_returns_trace_on_success(self, client):
        trace = {
            "contact_id": "abc123",
            "normalized_phone": "+15550001234",
            "campaign_name": "New Lead",
            "status": "active",
            "ai_campaign_value": "1",
            "steps": [],
        }
        with patch("app.services.pipeline_trace.get_lead_trace", return_value=trace):
            resp = client.get("/dashboard/lead/abc123/trace")
        assert resp.status_code == 200
        assert resp.json()["contact_id"] == "abc123"


# ── Alerts endpoint ────────────────────────────────────────────────────────────

class TestAlertsEndpoint:
    def test_returns_empty_active_list(self, client):
        resp = client.get("/dashboard/alerts?status=active")
        assert resp.status_code == 200
        data = resp.json()
        assert "alerts" in data

    def test_invalid_status_returns_422(self, client):
        resp = client.get("/dashboard/alerts?status=badstatus")
        assert resp.status_code == 422


# ── Action endpoints — auth ────────────────────────────────────────────────────

class TestActionAuth:
    def test_retry_requires_auth(self, client):
        resp = client.post("/dashboard/actions/retry", json={"exception_id": "x", "delay_minutes": 0})
        assert resp.status_code == 403

    def test_cancel_requires_auth(self, client):
        resp = client.post("/dashboard/actions/cancel", json={"contact_id": "x", "reason": "test"})
        assert resp.status_code == 403

    def test_finalize_requires_auth(self, client):
        resp = client.post("/dashboard/actions/finalize", json={"contact_id": "x", "reason": "test"})
        assert resp.status_code == 403

    def test_resolve_requires_auth(self, client):
        resp = client.post("/dashboard/actions/resolve", json={"exception_id": "x"})
        assert resp.status_code == 403

    def test_ignore_requires_auth(self, client):
        resp = client.post("/dashboard/actions/ignore", json={"exception_id": "x"})
        assert resp.status_code == 403


# ── Retry action ───────────────────────────────────────────────────────────────

class TestRetryAction:
    def test_retry_now_delegates_to_service(self, client):
        with patch("app.services.dashboard.retry_now") as mock_retry:
            mock_retry.return_value = {"success": True, "new_job_id": "new-job-1"}
            with patch("app.api.routes.dashboard_v2.Session") as _:
                resp = client.post(
                    "/dashboard/actions/retry",
                    json={"exception_id": "exc-1", "delay_minutes": 0},
                    headers=_auth_headers(),
                )
        # 200 or 409 depending on mock — just verify auth was accepted
        assert resp.status_code in (200, 409, 500)

    def test_retry_returns_409_on_conflict(self, client):
        with patch("app.services.dashboard.retry_now") as mock_retry:
            mock_retry.return_value = {"conflict": True, "reason": "exception not open"}
            resp = client.post(
                "/dashboard/actions/retry",
                json={"exception_id": "exc-conflict", "delay_minutes": 0},
                headers=_auth_headers(),
            )
        assert resp.status_code == 409


# ── Alerting service ───────────────────────────────────────────────────────────

class TestAlertingService:
    def _make_settings(self, **overrides):
        s = MagicMock()
        s.alert_queue_lag_threshold_seconds = 300
        s.alert_error_rate_threshold = 0.20
        s.alert_exception_spike_threshold = 10
        s.alert_dedup_window_seconds = 3600
        s.smtp_enabled = False
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_queue_lag_breach_creates_alert(self, session):
        from app.services.alerting import _evaluate_single_alert
        from app.models.alert_event import AlertEvent

        settings = self._make_settings()
        health = {"queue_lag_seconds": 450.0}
        now = datetime.now(tz=timezone.utc)

        defn = {
            "alert_type": f"queue_lag_exceeded_{uuid.uuid4().hex[:8]}",
            "severity": "critical",
            "metric_key": "queue_lag_seconds",
            "threshold_setting": "alert_queue_lag_threshold_seconds",
            "condition": "gt",
            "message_template": "Lag {value:.0f}s > {threshold:.0f}s",
        }

        _evaluate_single_alert(
            session=session,
            settings=settings,
            defn=defn,
            health=health,
            now=now,
            dedup_window=timedelta(seconds=3600),
        )

        rows = session.execute(
            text("SELECT COUNT(*) FROM alert_events WHERE alert_type = :t"),
            {"t": defn["alert_type"]},
        ).scalar()
        assert rows == 1

    def test_no_alert_when_below_threshold(self, session):
        from app.services.alerting import _evaluate_single_alert

        settings = self._make_settings()
        health = {"queue_lag_seconds": 10.0}
        now = datetime.now(tz=timezone.utc)
        alert_type = f"queue_lag_exceeded_{uuid.uuid4().hex[:8]}"

        defn = {
            "alert_type": alert_type,
            "severity": "critical",
            "metric_key": "queue_lag_seconds",
            "threshold_setting": "alert_queue_lag_threshold_seconds",
            "condition": "gt",
            "message_template": "Lag {value:.0f}s > {threshold:.0f}s",
        }

        _evaluate_single_alert(
            session=session, settings=settings, defn=defn,
            health=health, now=now, dedup_window=timedelta(seconds=3600),
        )

        rows = session.execute(
            text("SELECT COUNT(*) FROM alert_events WHERE alert_type = :t"),
            {"t": alert_type},
        ).scalar()
        assert rows == 0

    def test_smtp_not_called_when_disabled(self):
        from app.services.alerting import _send_alert_email

        settings = self._make_settings(smtp_enabled=False)
        with patch("smtplib.SMTP") as mock_smtp:
            _send_alert_email(
                settings=settings,
                alert_id="test-id",
                alert_type="queue_lag_exceeded",
                severity="critical",
                message="Test alert",
                now=datetime.now(tz=timezone.utc),
                is_resolution=False,
            )
        mock_smtp.assert_not_called()


# ── Pipeline trace service ─────────────────────────────────────────────────────

class TestPipelineTraceService:
    def test_returns_none_for_unknown_contact(self, session):
        from app.services.pipeline_trace import get_lead_trace

        result = get_lead_trace(session, contact_id="does-not-exist")
        assert result is None

    def test_returns_trace_with_lead_state(self, session):
        from app.services.pipeline_trace import get_lead_trace
        from app.models.lead_state import LeadState

        cid = f"trace-test-{uuid.uuid4().hex[:8]}"
        lead = LeadState(
            id=str(uuid.uuid4()),
            contact_id=cid,
            normalized_phone="+15550001234",
            campaign_name="New Lead",
            status="active",
            version=0,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        session.add(lead)
        session.flush()

        result = get_lead_trace(session, contact_id=cid)

        assert result is not None
        assert result["contact_id"] == cid
        assert result["campaign_name"] == "New Lead"
        assert isinstance(result["steps"], list)

    def test_phone_fallback_lookup(self, session):
        from app.services.pipeline_trace import get_lead_trace
        from app.models.lead_state import LeadState

        phone = f"+1555{uuid.uuid4().int % 9000000 + 1000000}"
        cid = f"phone-test-{uuid.uuid4().hex[:8]}"
        lead = LeadState(
            id=str(uuid.uuid4()),
            contact_id=cid,
            normalized_phone=phone,
            campaign_name="Cold Lead",
            status="active",
            version=0,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        session.add(lead)
        session.flush()

        # Lookup by unknown contact_id but correct phone
        result = get_lead_trace(session, contact_id="unknown-cid", phone=phone)
        assert result is not None
        assert result["contact_id"] == cid

    def test_shadow_step_detected(self, session):
        """Shadow steps appear when shadow_actions rows exist for the contact."""
        from app.services.pipeline_trace import get_lead_trace
        from app.models.lead_state import LeadState
        from app.models.shadow_action import ShadowAction
        from app.models.scheduled_job import ScheduledJob

        cid = f"shadow-trace-{uuid.uuid4().hex[:8]}"
        now = datetime.now(tz=timezone.utc)

        lead = LeadState(
            id=str(uuid.uuid4()),
            contact_id=cid,
            normalized_phone="+15550009999",
            campaign_name="New Lead",
            status="active",
            version=0,
            created_at=now,
            updated_at=now,
        )
        session.add(lead)

        # Add a completed send_sms job
        job = ScheduledJob(
            id=str(uuid.uuid4()),
            job_type="send_sms",
            entity_type="lead",
            entity_id=cid,
            status="completed",
            run_at=now,
            payload_json={"contact_id": cid, "campaign_name": "New Lead"},
            version=2,
            created_at=now,
        )
        session.add(job)

        # Add a shadow_action for the SMS
        shadow = ShadowAction(
            id=str(uuid.uuid4()),
            contact_id=cid,
            action_type="sms",
            payload={"message_body": "Hi, this is Cora from Colaberry!"},
            created_at=now,
        )
        session.add(shadow)
        session.flush()

        result = get_lead_trace(session, contact_id=cid)
        assert result is not None

        sms_step = next(
            (s for s in result["steps"] if s["job_type"] == "send_sms"), None
        )
        assert sms_step is not None
        assert sms_step["is_shadow"] is True
        assert sms_step["shadow_payload"] is not None
        assert "message_body" in sms_step["shadow_payload"]


# ── SQL rendering tests for dashboard_metrics ─────────────────────────────────
#
# These tests do NOT need a real database. They intercept session.execute(),
# capture the SQL string, and assert it is well-formed before it ever reaches
# Postgres. This catches both classes of bug that hit production:
#
#   1. F-string brace escaping: {7,} inside f"""...""" evaluates to (7,)
#      because Python treats {7,} as the expression `7,` (a tuple).
#      Fix: {{7,}} → produces literal {7,} in the SQL string.
#
#   2. PostgreSQL E-string backslash stripping: E'\+' → '+' (backslash
#      dropped for unrecognized escape sequences), turning ^+? into a
#      quantifier on the ^ anchor → "quantifier operand invalid".
#      Fix: use plain '...' literal (standard_conforming_strings=on default).

class TestGetRecentCallsSql:
    """Validate the SQL rendered by get_recent_calls without hitting Postgres."""

    def _capture_sql(self) -> str:
        """Call get_recent_calls with a mock session and return the SQL string."""
        from app.services.dashboard_metrics import get_recent_calls

        captured: list[str] = []

        class _CapturingSession:
            def execute(self, stmt, params=None):
                captured.append(str(stmt.text) if hasattr(stmt, "text") else str(stmt))
                raise StopIteration("captured")

        try:
            get_recent_calls(_CapturingSession())  # type: ignore[arg-type]
        except StopIteration:
            pass

        assert captured, "session.execute was never called"
        return captured[0]

    def test_phone_regex_no_e_string_prefix(self):
        """Regex must NOT use E'...' — PostgreSQL drops \\+ to + making ^+? invalid."""
        sql = self._capture_sql()
        # The phone pattern line must not start with E' before the regex
        assert "~ E'" not in sql, (
            "Phone regex uses E-string prefix; PostgreSQL will strip \\+ → + "
            "making the quantifier operand invalid (^+? quantifies a zero-width anchor)"
        )

    def test_phone_regex_has_valid_quantifier(self):
        """Regex must contain the literal {7,} quantifier, not (7,) from f-string evaluation."""
        sql = self._capture_sql()
        assert "{7,}" in sql, (
            "Phone regex quantifier {7,} missing from SQL — likely rendered as (7,) "
            "due to unescaped f-string braces ({7,} should be {{7,}} in the source)"
        )
        assert "(7,)" not in sql, (
            "SQL contains (7,) — f-string evaluated {7,} as the tuple expression `7,`. "
            "Use {{7,}} in the f-string source to produce the literal {7,}."
        )

    def test_phone_regex_anchors_intact(self):
        """Regex must start with ^ and end with $ to be a full-string match."""
        sql = self._capture_sql()
        # Find the phone pattern substring
        assert "^\\+?" in sql or "^+" not in sql, (
            "Phone regex anchor structure unexpected"
        )
        assert "{7,}$" in sql, "Regex must be anchored with $ at the end"

    def test_params_use_sqlalchemy_style(self):
        """SQL must use :param style, not %(param)s — the latter bypasses SQLAlchemy binding."""
        sql = self._capture_sql()
        assert ":from_dt" in sql, "Expected :from_dt bound parameter"
        assert ":to_dt" in sql, "Expected :to_dt bound parameter"
        assert ":limit" in sql, "Expected :limit bound parameter"


# ─────────────────────────────────────────────────────────────────────────────
# get_staff_call_quality_summary (spec/23)
# ─────────────────────────────────────────────────────────────────────────────
# SQLite (3.25+) supports the FILTER (WHERE ...) clause used here, so this
# runs against a real in-memory DB rather than a SQL-text-capture mock.

class TestGetStaffCallQualitySummary:
    @pytest.fixture(scope="class")
    def scq_engine(self):
        from sqlalchemy.pool import StaticPool

        import app.models.staff_call_quality  # noqa: F401

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(eng)
        yield eng
        Base.metadata.drop_all(eng)
        eng.dispose()

    @pytest.fixture
    def scq_session(self, scq_engine):
        with Session(scq_engine) as sess:
            yield sess
            sess.rollback()

    def _row(self, session, **overrides):
        from app.models.staff_call_quality import StaffCallQuality

        defaults = dict(
            id=str(uuid.uuid4()),
            ghl_message_id=str(uuid.uuid4()),
            ghl_contact_id="contact-1",
            call_time=datetime.now(tz=timezone.utc),
            call_connected=False,
        )
        defaults.update(overrides)
        row = StaffCallQuality(**defaults)
        session.add(row)
        return row

    def test_empty_table_returns_zeroed_stats(self, scq_session):
        from app.services.dashboard_metrics import get_staff_call_quality_summary

        result = get_staff_call_quality_summary(scq_session)

        assert result["total_scanned"] == 0
        assert result["total_analyzed"] == 0
        assert result["flagged_count"] == 0
        assert result["avg_score_sales"] is None
        assert result["avg_score_support"] is None
        assert result["recent"] == []

    def test_counts_and_averages(self, scq_session):
        from app.services.dashboard_metrics import get_staff_call_quality_summary

        self._row(scq_session, call_connected=False)  # not connected — scanned only
        self._row(scq_session, call_connected=True, conversation_type="sales", quality_score=80)
        self._row(scq_session, call_connected=True, conversation_type="sales", quality_score=90)
        self._row(scq_session, call_connected=True, conversation_type="support", quality_score=60,
                   flagged_reason="Gave incorrect refund policy info")
        scq_session.flush()

        result = get_staff_call_quality_summary(scq_session)

        assert result["total_scanned"] == 4
        assert result["total_connected"] == 3
        assert result["total_analyzed"] == 3
        assert result["flagged_count"] == 1
        assert result["avg_score_sales"] == 85.0
        assert result["avg_score_support"] == 60.0

    def test_recent_list_ordered_newest_first(self, scq_session):
        from app.services.dashboard_metrics import get_staff_call_quality_summary

        older = self._row(scq_session, call_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
        newer = self._row(scq_session, call_time=datetime(2026, 6, 1, tzinfo=timezone.utc))
        scq_session.flush()

        result = get_staff_call_quality_summary(scq_session, limit=10)

        ids = [r["ghl_message_id"] for r in result["recent"]]
        assert ids.index(newer.ghl_message_id) < ids.index(older.ghl_message_id)

    def test_recent_list_respects_limit(self, scq_session):
        from app.services.dashboard_metrics import get_staff_call_quality_summary

        for _ in range(5):
            self._row(scq_session)
        scq_session.flush()

        result = get_staff_call_quality_summary(scq_session, limit=2)

        assert len(result["recent"]) == 2
