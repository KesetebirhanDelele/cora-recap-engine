"""Voice-call audit (spec/35): what GHL's AI Receptionist said on a call is scanned like a text / email, bot lines only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest

from app.core import offer
from app.core import voice_audit as va
from app.services import channel_health as chs
from app.services import voice_audit as svc

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)
TERMS = offer.parse_terms(None)
RECEPTIONIST = "6a97318a65c4b663f8b154a1"

# Shape of the real 2026-10-15 call (names, address and phone removed).
ABERE = (
    "bot:Thank you for calling Colaberry. How can I help?\n"
    "human:I need to start a SQL developer course.\n"
    "bot:To start a SQL developer course at Colaberry, you can go to training dot colaberry dot com to enroll, or create a free "
    "account at my free AI class dot com. There is no payment needed.\n"
    "human:But how much is the payment?\n"
    "bot:Colaberry offers three payment plans:\n"
    "- You can start with the AI Career Readiness Starter for forty-nine dollars. This includes SQL Server installation.\n"
    "- The Standard Plan is five hundred dollars down.\n"
    "You can explore the material for free before committing.\n"
)


# ───────────────────────── pure logic ─────────────────────────

def test_transcript_turns_and_continuation_lines():
    turns = va.parse_transcript(ABERE)
    assert [w for w, _ in turns] == ["bot", "human", "bot", "human", "bot"]
    assert "forty-nine dollars" in turns[-1][1] and "five hundred dollars" in turns[-1][1]   # bullet lines stay in the bot turn


def test_the_abere_call_is_flagged_for_sql_and_free():
    f = va.scan_call(ABERE, TERMS)
    assert f is not None and {"sql", "free"} <= set(f.terms)
    assert f.course_only is False                       # "free" is a hard term: the offer judge may never clear it
    assert "SQL developer course" in f.excerpt
    assert f.prior_human and "SQL developer" in f.prior_human


def test_caller_words_alone_are_never_a_violation():
    assert va.scan_call("human:I want the SQL course, is it free?\nbot:Thanks, a team member will call you back.", TERMS) is None


@pytest.mark.parametrize("said", [
    "Go to training dot colaberry dot com slash start dash free to begin.",
    "Go to training.colaberry.com/start-free to begin.",
    "Go to Training Dot Colaberry Dot Com Slash Start Hyphen Free.",
])
def test_the_approved_link_may_be_said_or_written(said):
    assert va.scan_call("bot:" + said, TERMS) is None


@pytest.mark.parametrize("said", [
    "Create a free account at my free AI class dot com.",                                    # the old domain
    "Go to training dot colaberry dot com slash start dash free dash trial.",                # a different path
    "Start free today at training dot colaberry dot com slash start dash free.",             # the WORD outside the link
])
def test_free_outside_the_approved_link_is_flagged(said):
    f = va.scan_call("bot:" + said, TERMS)
    assert f is not None and "free" in f.terms


def test_employment_claim_is_flagged_and_is_a_hard_term():
    f = va.scan_call("bot:We have a job guarantee for every graduate.", TERMS)
    assert f is not None and "job guarantee" in f.terms and f.course_only is False


def test_course_name_only_is_judgeable():
    f = va.scan_call("bot:Colaberry no longer offers the SQL course.", TERMS)
    assert f is not None and f.terms == ["sql"] and f.course_only is True


def test_company_name_in_the_greeting_is_not_a_hit():
    assert va.scan_call("bot:Welcome to Colaberry School of Data Analytics.", TERMS, ("School of Data Analytics",)) is None


def test_origin_labels():
    assert va.origin_label(RECEPTIONIST) == "GHL AI Receptionist (voice call)"
    assert va.origin_label("zzzz111111") == "GHL Voice AI agent ...111111 (voice call)"       # unknown agent: tail of its id
    assert va.origin_label(None) == "GHL Voice AI agent ...unknown (voice call)"


# ───────────────────────── service (fake session) ─────────────────────────

class FakeSession:
    def __init__(self, seen=(), active_alert=False):
        self.seen, self.active_alert = list(seen), active_alert
        self.inserts, self.updates, self.added, self.rolled_back = [], [], [], 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        res = MagicMock()
        if "SELECT external_id FROM channel_events" in sql:
            ids = set(params["ids"])
            res.fetchall.return_value = [(i,) for i in self.seen if i in ids]
        elif "SELECT id FROM alert_events" in sql:
            res.fetchone.return_value = ("existing",) if self.active_alert else None
        elif "INSERT INTO channel_events" in sql:
            self.inserts.append(params)
        elif "UPDATE alert_events" in sql:
            self.updates.append((sql, params))
        return res

    def add(self, obj):
        self.added.append(obj)

    def rollback(self):
        self.rolled_back += 1

    def commit(self):
        pass


def _log(i, transcript, agent=RECEPTIONIST, age_min=30, contact="c1"):
    return {"id": i, "agentId": agent, "contactId": contact, "fromNumber": "+15550001111", "duration": 120,
            "createdAt": (NOW - timedelta(minutes=age_min)).isoformat().replace("+00:00", "Z"), "transcript": transcript}


class FakeGHL:
    def __init__(self, pages=None, error=None):
        self.pages, self.error, self.calls = pages or [], error, 0

    def list_voice_call_logs(self, page=1, page_size=25):
        self.calls += 1
        if self.error:
            raise self.error
        return self.pages[page - 1] if page <= len(self.pages) else []


def _kinds(session):
    return [(p["k"], p["x"]) for p in session.inserts]


def test_a_flagged_call_is_recorded_with_its_origin_and_what_the_agent_said():
    s = FakeSession()
    out = svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[_log("call1", ABERE)]]), judge=MagicMock())
    assert out["flagged"] == 1 and out["error"] is None
    flag = next(p for p in s.inserts if p["k"] == "content_flag")
    assert flag["x"] == "call1" and flag["src"] == "ghl_voice_ai" and "sql" in flag["e"] and "free" in flag["e"]
    d = json.loads(flag["d"])
    assert d["origin"] == "GHL AI Receptionist (voice call)" and "SQL developer course" in d["snippet"]
    assert d["agent_id"] == RECEPTIONIST and d["call_id"] == "call1"


def test_a_clean_call_is_marked_scanned_and_not_flagged():
    s = FakeSession()
    out = svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[_log("ok1", "bot:A team member will call you back.")]]), judge=MagicMock())
    assert _kinds(s) == [("voice_seen", "ok1")] and out["flagged"] == 0


def test_replay_does_not_scan_or_flag_the_same_call_twice():
    s = FakeSession(seen=["call1"])
    judge = MagicMock()
    out = svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[_log("call1", ABERE)]]), judge=judge)
    assert s.inserts == [] and out["scanned"] == 0 and out["flagged"] == 0


def test_the_judge_may_clear_a_course_name_only_call_but_never_a_hard_term():
    refusal = _log("r1", "human:Do you teach SQL?\nbot:Colaberry no longer offers the SQL course.")
    judge = MagicMock(return_value="not_offering")
    s = FakeSession()
    out = svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[refusal]]), judge=judge)
    assert out["cleared"] == 1 and out["flagged"] == 0
    assert ("content_ok", "r1") in _kinds(s) and ("content_flag", "r1") not in _kinds(s)

    judge2, s2 = MagicMock(return_value="not_offering"), FakeSession()
    out2 = svc.audit_voice_calls(s2, None, NOW, ghl=FakeGHL([[_log("h1", ABERE)]]), judge=judge2)
    judge2.assert_not_called()                          # "free" is in ABERE: hard term, no judge
    assert out2["flagged"] == 1


@pytest.mark.parametrize("verdict", ["offers", "unclear"])
def test_any_doubt_from_the_judge_keeps_the_call_flagged(verdict):
    s = FakeSession()
    svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[_log("d1", "bot:Yes, the SQL course is available.")]]),
                          judge=MagicMock(return_value=verdict))
    assert ("content_flag", "d1") in _kinds(s)


def test_a_judge_crash_keeps_the_call_flagged():
    s = FakeSession()
    svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[_log("d2", "bot:The SQL course is open.")]]),
                          judge=MagicMock(side_effect=RuntimeError("boom")))
    assert ("content_flag", "d2") in _kinds(s)


def test_a_failed_read_raises_one_labelled_alert_and_marks_nothing_clean():
    s = FakeSession()
    with patch("app.services.alerting._send_alert_email") as mail:
        out = svc.audit_voice_calls(s, NS(), NOW, ghl=FakeGHL(error=RuntimeError("401 not authorized for this scope")))
    assert out["error"] and s.inserts == [] and s.rolled_back == 1
    assert len(s.added) == 1 and s.added[0].alert_type == "ghl_voice_audit_error"
    assert "AI Receptionist call audit" in s.added[0].message and "not a problem in the AI Receptionist" in s.added[0].message
    mail.assert_called_once()


def test_a_failed_read_does_not_alert_twice_while_one_is_active():
    s = FakeSession(active_alert=True)
    with patch("app.services.alerting._send_alert_email") as mail:
        svc.audit_voice_calls(s, NS(), NOW, ghl=FakeGHL(error=RuntimeError("down")))
    assert s.added == [] and mail.call_count == 0


def test_a_good_read_resolves_the_failure_alert():
    s = FakeSession()
    svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[]]), judge=MagicMock())
    assert any("status = 'resolved'" in sql for sql, _ in s.updates)


def test_a_call_without_a_transcript_waits_then_is_given_up_on():
    fresh, old = _log("n1", "", age_min=10), _log("n2", "", age_min=300)
    s = FakeSession()
    out = svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[fresh, old]]), judge=MagicMock())
    assert out["waiting"] == 1
    assert _kinds(s) == [("voice_seen", "n2")]


def test_calls_older_than_the_lookback_window_are_ignored():
    s = FakeSession()
    out = svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[_log("old", ABERE, age_min=60 * 80)]]), judge=MagicMock())
    assert s.inserts == [] and out["scanned"] == 0


def test_paging_stops_once_a_page_has_an_already_scanned_call():
    s = FakeSession(seen=["a2"])
    g = FakeGHL([[_log("a1", "bot:ok"), _log("a2", "bot:ok")], [_log("b1", "bot:ok")]])
    svc.audit_voice_calls(s, None, NOW, ghl=g, judge=MagicMock(), page_size=2)
    assert g.calls == 1


def test_it_can_be_switched_off():
    with patch("app.core.app_config.get_config_value", return_value="false"):
        out = svc.audit_voice_calls(FakeSession(), NS(), NOW, ghl=FakeGHL(error=RuntimeError("never called")))
    assert out["skipped"] is True and out["error"] is None


# ───────────────────────── alert wording ─────────────────────────

def _row(channel, source="ghl_voice_ai", detail=None, cid="", error="sql, free", ext="x1"):
    return (ext, cid, channel, error, source, NOW, detail)


def test_a_voice_alert_line_says_it_came_from_the_ai_receptionist():
    line = chs._flag_line(None, _row("voice", detail={"origin": "GHL AI Receptionist (voice call)", "snippet": "you can enroll in SQL",
                                                       "from_number": "+15550001111"}))
    assert "[origin: GHL AI Receptionist (voice call)" in line and "the agent said" in line and "call id x1" in line
    assert "+15550001111" in line


def test_a_text_alert_line_names_its_ghl_source():
    with patch.object(chs, "_lead_line", return_value="Lead A"):
        line = chs._flag_line(None, _row("sms", source="app", cid="c1", error="free", ext="m1"))
    assert "sms sent by app" in line and "[origin: GHL sms with source 'app'" in line


# ───────────────────────── database column widths (production bug 2026-10-20) ─────────────────────────

def test_every_value_written_fits_the_channel_events_columns():
    """channel_events.kind is varchar(12), channel varchar(10), source varchar(30). 'voice_scanned' (13) broke the first
    production run; the fake session above cannot see column widths, so pin them here."""
    s = FakeSession()
    svc.audit_voice_calls(s, None, NOW, ghl=FakeGHL([[
        _log("w1", ABERE), _log("w2", "bot:ok"), _log("w3", "human:SQL?\nbot:Colaberry no longer offers the SQL course."),
        _log("w4", "", age_min=300)]]), judge=MagicMock(return_value="not_offering"))
    kinds = {p["k"] for p in s.inserts}
    assert {"content_flag", "content_ok", "voice_seen"} <= kinds
    for p in s.inserts:
        assert len(p["k"]) <= 12 and len("voice") <= 10 and len(p["src"]) <= 30, p
        assert len(p["x"]) <= 100 and len(p["c"]) <= 100 and len(p["e"] or "") <= 300
