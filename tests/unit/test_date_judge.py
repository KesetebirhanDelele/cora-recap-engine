"""AI check for the wrong-date monitor (spec/32): the rule engine guesses which event a date belongs to from nearby words;
the LLM reads the whole message. No DB, no real LLM: the OpenAI client and the SQL session are mocked."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.wrong_date_guard import find_wrong_dates
from app.services import date_judge as dj
from app.services import wrong_date_monitor as wdm

CLASS, OH = "November 12, 2026", "October 29, 2026"
# The real email of 2026-10-14 17:16 CDT: both dates are right, the rule engine read Nov 12 as the Open House date.
REAL_OK_EMAIL = (
    "If you're interested, you can start the self-paced option anytime with $0 to start and no card needed at https://training.colaberry.com/start-free. "
    "Alternatively, if you prefer live classes, we have an Open House on October 29, 2026, where you can learn more and join "
    "the next session starting on November 12, 2026. You can RSVP here: Open House RSVP.")
NOW = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_cache():
    dj._cache.clear()
    yield
    dj._cache.clear()


def _client(*votes):
    c = MagicMock()
    c.chat_completion.side_effect = [v if isinstance(v, Exception) else {"verdict": v} for v in votes]
    return c


def _review(*votes, mid="m1", body=REAL_OK_EMAIL, flagged=None, cls=CLASS, oh=OH):
    flagged = flagged if flagged is not None else [{"kind": "open_house", "raw": "November 12, 2026", "expected": OH}]
    c = _client(*votes)
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        return dj.review(SimpleNamespace(), mid, "Following up", body, flagged, cls, oh), c


def test_precondition_the_rule_engine_does_flag_the_real_email():
    assert find_wrong_dates(REAL_OK_EMAIL, None, expected_class_start=CLASS, expected_open_house=OH)


@pytest.mark.parametrize("votes,want", [
    (("correct", "correct"), dj.CORRECT),
    (("correct", "wrong"), dj.WRONG),
    (("wrong", "correct"), dj.WRONG),
    (("wrong", "wrong"), dj.WRONG),
    (("correct", "unclear"), dj.UNCLEAR),            # a split never suppresses an alert
    (("unclear", "unclear"), dj.UNCLEAR),
    (("correct", "maybe"), dj.UNCLEAR),              # off-schema vote counts as unclear
])
def test_only_a_unanimous_correct_suppresses(votes, want):
    verdict, c = _review(*votes)
    assert verdict == want and c.chat_completion.call_count == dj.VOTES


def test_a_failed_ask_is_unclear_and_not_cached():
    verdict, _ = _review("correct", RuntimeError("boom"), mid="mf")
    assert verdict == dj.UNCLEAR and "mf" not in dj._cache


def test_verdict_is_cached_per_message():
    c = _client("correct", "correct")
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        dj.review(SimpleNamespace(), "mc", None, "x", [], CLASS, OH)
        dj.review(SimpleNamespace(), "mc", None, "x", [], CLASS, OH)
    assert c.chat_completion.call_count == dj.VOTES


def test_prompt_carries_the_truth_the_flagged_dates_and_the_whole_message():
    _, c = _review("correct", "correct", body=("Intro. " * 150) + REAL_OK_EMAIL)
    user = c.chat_completion.call_args_list[0].args[0][1]["content"]
    assert CLASS in user and OH in user and "'November 12, 2026' (rule engine took it for the open house)" in user
    assert "join the next session starting on November 12, 2026" in user          # the part past 800 chars is not cut off


def test_blank_truth_is_described_as_nothing_scheduled():
    _, c = _review("wrong", "wrong", cls="", oh="")
    assert "(nothing scheduled)" in c.chat_completion.call_args_list[0].args[0][1]["content"]


# ── scan_wrong_dates wiring (mocked session; SQL itself is covered by the opt-in DB tests) ───────────────────────

class _Res:
    def __init__(self, rows=None, one=None):
        self._rows, self._one = rows or [], one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def _scan(monkeypatch, verdict, n_rows=1, config=None, body=REAL_OK_EMAIL):
    """Run scan_wrong_dates over n flagged messages; returns (inserted_params_list, alert_email_mock, new_count)."""
    rows = [(f"msg{i}", f"c{i}", "email", "Following up", body, NOW) for i in range(n_rows)]
    inserts = []

    class FakeSession:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "FROM outbound_messages" in sql:
                return _Res(rows=rows)
            if "INSERT INTO wrong_date_incidents" in sql:
                inserts.append(params)
                return _Res(one=("inserted",))
            return _Res()

    cfg = {"date_llm_enabled": "true", **(config or {})}
    monkeypatch.setattr(wdm, "expected_dates", lambda s, st: (CLASS, OH))
    monkeypatch.setattr(wdm, "_key_updated_at", lambda s, k: None)
    monkeypatch.setattr(wdm, "_sync_aggregate_alert", lambda s, n: None)
    monkeypatch.setattr(wdm, "_config", lambda s, st, k, d="": cfg.get(k, d))
    email = MagicMock()
    monkeypatch.setattr("app.services.alerting._send_alert_email", email)
    judged = []
    monkeypatch.setattr(dj, "review", lambda *a, **kw: judged.append(a[1]) or verdict)
    n = wdm.scan_wrong_dates(FakeSession(), SimpleNamespace(frontend_url="https://x.test"), NOW)
    return inserts, email, n, judged


def test_correct_dates_are_recorded_as_dismissed_with_no_alert_email(monkeypatch):
    inserts, email, n, judged = _scan(monkeypatch, dj.CORRECT)
    assert n == 0 and email.call_count == 0 and judged == ["msg0"]
    assert inserts[0]["status"] == "dismissed" and inserts[0]["rby"] == wdm.AI_DISMISSED_BY and inserts[0]["rat"] == NOW


@pytest.mark.parametrize("verdict", [dj.WRONG, dj.UNCLEAR])
def test_wrong_or_unclear_raises_the_incident_and_emails_as_before(monkeypatch, verdict):
    inserts, email, n, _ = _scan(monkeypatch, verdict)
    assert n == 1 and email.call_count == 1
    assert inserts[0]["status"] == "open" and inserts[0]["rby"] is None and inserts[0]["rat"] is None


def test_switching_the_ai_check_off_keeps_the_old_behaviour(monkeypatch):
    inserts, email, n, judged = _scan(monkeypatch, dj.CORRECT, config={"date_llm_enabled": "false"})
    assert judged == [] and n == 1 and email.call_count == 1 and inserts[0]["status"] == "open"


def test_messages_beyond_the_per_scan_budget_are_raised_unreviewed(monkeypatch):
    monkeypatch.setattr(wdm, "AI_CHECKS_PER_SCAN", 1)
    inserts, email, n, judged = _scan(monkeypatch, dj.CORRECT, n_rows=2)
    assert judged == ["msg0"] and [i["status"] for i in inserts] == ["dismissed", "open"] and n == 1 and email.call_count == 1
