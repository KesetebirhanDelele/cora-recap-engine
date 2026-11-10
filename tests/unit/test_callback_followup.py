"""Staff call-back follow-up (spec/50): pure decisions, the ledger, booking, staff email, unanswered lead emails. No network, no real GHL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core import callback_followup as cf
from app.models.base import Base
from app.models.callback_followup import CallbackFollowup
from app.models.staff_call_quality import StaffCallQuality
from app.services import callback_followup as svc

CT = ZoneInfo("America/Chicago")
# Monday 2026-11-09 16:15 Central (the New Lead call of 2026-11-09 22:14 UTC)
NOW = datetime(2026, 11, 9, 22, 15, tzinfo=timezone.utc)
DIR = cf.parse_directory(None)
ROSE, TAIWO = DIR[0], DIR[1]


def slot(day, hh, mm=0):
    return datetime(2026, 11, day, hh, mm, tzinfo=CT).isoformat()


# ── staff directory ─────────────────────────────────────────────────────────
def test_default_directory_has_the_five_staff_with_calendars_and_emails():
    assert {p["key"] for p in DIR} == {"roselen", "taiwo", "balakrishna", "farhat", "balamurali"}
    assert all(p["email"].endswith("@colaberry.com") and p["calendar_id"] and p["ghl_id"] for p in DIR)
    assert next(p for p in DIR if p["key"] == "balakrishna")["email"] == "balakrishna.k@colaberry.com"


def test_bad_directory_setting_falls_back_to_the_default():
    assert cf.parse_directory("not json") == DIR and cf.parse_directory("[]") == DIR and cf.parse_directory("") == DIR


@pytest.mark.parametrize("kind,target,want", [
    ("support", "customer_support_bala", "balakrishna"),
    ("support", "customer_support_balamurali", "balamurali"),          # 'bala' must not capture 'balamurali'
    ("support", "customer_support_farhat", "farhat"),
    ("admissions", "admissions_transfer_roselen", "roselen"),
    ("admissions", "", "roselen"),
    ("ipbc", "ipbc_transfer_taiwo", "taiwo"),
])
def test_pick_staff_names_the_person_the_caller_was_sent_to(kind, target, want):
    staff, notify = cf.pick_staff(kind, target, DIR)
    assert staff["key"] == want and [p["key"] for p in notify] == [want]


def test_an_unknown_support_target_emails_every_support_person_and_never_books():
    staff, notify = cf.pick_staff("support", "customer_support_unknown", DIR)
    assert staff is None and {p["key"] for p in notify} == {"balakrishna", "farhat", "balamurali"}


# ── unnamed support request -> whoever is on shift, else the next person (Kes 2026-11-10) ──
ROSTER = [  # the production support_staff_roster, Central time
    {"name": "Balakrishna K", "ghl_id": "yIhCTptvoNLixaWkLcRd", "days": [0, 1, 2, 3, 4], "shift_start": "04:30", "shift_end": "12:30"},
    {"name": "Farhat Beig", "ghl_id": "RBa4nlSSiWeu4CoGFVyl", "days": [0, 1, 2, 3, 4], "shift_start": "14:00", "shift_end": "22:00"},
    {"name": "Balamurali Kasula", "ghl_id": "vn8w0WF0pZcoszVn4Ll1", "days": [0, 1, 2, 3, 4], "shift_start": "18:00", "shift_end": "02:00"},
    {"name": "Balamurali Kasula", "ghl_id": "vn8w0WF0pZcoszVn4Ll1", "days": [5], "shift_start": "09:00", "shift_end": "01:00"},
]


@pytest.mark.parametrize("when,want", [
    (datetime(2026, 11, 9, 16, 15, tzinfo=CT), "farhat"),            # Monday 16:15: Farhat is on shift
    (datetime(2026, 11, 9, 19, 0, tzinfo=CT), "farhat"),             # two on shift: the first on the roster
    (datetime(2026, 11, 9, 8, 0, tzinfo=CT), "balakrishna"),
    (datetime(2026, 11, 9, 13, 0, tzinfo=CT), "farhat"),             # nobody on shift: the next shift to start (Farhat 14:00)
    (datetime(2026, 11, 7, 3, 0, tzinfo=CT), "balamurali"),          # Saturday 03:00: next is Balamurali's Saturday 09:00
    (datetime(2026, 11, 8, 12, 0, tzinfo=CT), "balakrishna"),        # Sunday noon: next is Monday 04:30
])
def test_an_unnamed_support_request_goes_to_who_is_on_shift_else_the_next_person(when, want):
    staff, notify = cf.pick_staff("support", "customer_support_unknown", DIR, ROSTER, when)
    assert staff["key"] == want and [p["key"] for p in notify] == [want]


def test_a_named_support_person_still_wins_over_the_roster():
    staff, _ = cf.pick_staff("support", "customer_support_balamurali", DIR, ROSTER, datetime(2026, 11, 9, 8, 0, tzinfo=CT))
    assert staff["key"] == "balamurali"


def test_an_empty_or_unusable_roster_falls_back_to_all_support_staff():
    for roster in (None, [], [{"name": "Nobody Known", "days": [0], "shift_start": "09:00", "shift_end": "17:00"}]):
        staff, notify = cf.pick_staff("support", "customer_support_unknown", DIR, roster, datetime(2026, 11, 9, 10, 0, tzinfo=CT))
        assert staff is None and len(notify) == 3


# ── time handling ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,ok", [
    ("2026-11-10T15:30", True), ("2026-11-09T16:30", True), ("", False), (None, False), ("tomorrow", False),
    ("2026-11-09T16:20", False),                    # under 10 minutes away
    ("2026-11-09T10:00", False),                    # in the past
    ("2027-03-01T10:00", False),                    # more than 45 days ahead
])
def test_parse_requested_time(value, ok):
    assert (cf.parse_requested_time(value, NOW) is not None) is ok


def test_local_time_is_read_in_central():
    assert cf.parse_requested_time("2026-11-10T15:30", NOW) == datetime(2026, 11, 10, 15, 30, tzinfo=CT)


# ── day + clock -> date, worked out in code (the model only copies what was said) ──
REF = datetime(2026, 11, 9, 22, 14, tzinfo=timezone.utc)          # Monday 16:14 Central


@pytest.mark.parametrize("day,clock,want", [
    ("today", "17:00", "2026-11-09T17:00"), ("tomorrow", "14:00", "2026-11-10T14:00"), ("thursday", "10:30", "2026-11-12T10:30"),
    ("monday", "10:00", "2026-11-16T10:00"),             # same weekday, time already passed -> next week
    ("monday", "17:30", "2026-11-09T17:30"),             # same weekday, later today
    ("11-12", "16:00", "2026-11-12T16:00"), ("01-05", "09:00", "2027-01-05T09:00"),   # a passed month-day rolls to next year
    ("2026-11-20", "08:15", "2026-11-20T08:15"),
    ("", "14:00", None), ("tomorrow", "", None), ("someday", "14:00", None), ("tomorrow", "25:00", None), ("tomorrow", "2pm", None),
    ("13-45", "10:00", None),
])
def test_resolve_day_clock(day, clock, want):
    assert cf.resolve_day_clock(day, clock, REF) == want


# ── decide ──────────────────────────────────────────────────────────────────
def test_clear_time_and_free_slot_books():
    p = cf.decide(callback_wanted=True, time_is_clear=True, requested_local="2026-11-10T15:30", staff=ROSE,
                  free_slots=[slot(10, 15, 30)], now=NOW)
    assert p.action == cf.BOOK and p.start == datetime(2026, 11, 10, 15, 30, tzinfo=CT) and p.end - p.start == timedelta(minutes=30)


@pytest.mark.parametrize("req,clear,slots,staff,reason", [
    ("", False, [], ROSE, "no_time"),                                           # the 2026-11-09 'as soon as possible' call
    ("2026-11-10T15:30", False, [slot(10, 15, 30)], ROSE, "time_unclear"),
    ("2026-11-10T15:00", True, [slot(10, 15, 30)], ROSE, "slot_taken"),
    ("2026-11-10T15:30", True, [], ROSE, "slot_taken"),
    ("2026-11-09T10:00", True, [slot(10, 15, 30)], ROSE, "time_not_usable"),
    ("2026-11-10T15:30", True, [slot(10, 15, 30)], None, "no_calendar"),
])
def test_everything_else_is_a_staff_email_never_a_guess(req, clear, slots, staff, reason):
    p = cf.decide(callback_wanted=True, time_is_clear=clear, requested_local=req, staff=staff, free_slots=slots, now=NOW)
    assert p.action == cf.EMAIL_STAFF and p.reason == reason


# ── staff email ─────────────────────────────────────────────────────────────
def test_staff_email_has_the_lead_details_summary_and_no_transcript():
    lead = cf.Lead(name="Jacqulyn T", phone="+19728007667", email="j@example.com", campaign="New Lead")
    plan = cf.Plan(cf.EMAIL_STAFF, "no_time")
    subject, body = cf.build_staff_email(plan=plan, lead=lead, staff_names=["Roselen Flores"], source="call", source_id="e5b7413f",
                                         summary="Lead wants to talk to admissions about the program.", asked_for="support")
    assert subject == "Please call back: Jacqulyn T"
    for must in ("Hi Roselen", "+19728007667", "j@example.com", "New Lead", "No call-back time was given", "talk to admissions", "call e5b7413f"):
        assert must in body


def test_booked_email_names_the_time():
    start = datetime(2026, 11, 10, 15, 30, tzinfo=CT)
    plan = cf.Plan(cf.BOOK, "clear_time_free_slot", start=start, end=start + timedelta(minutes=30))
    subject, body = cf.build_staff_email(plan=plan, lead=cf.Lead(name="Sam"), staff_names=["Taiwo Oludimimu"], source="email",
                                         source_id="m1", summary="s")
    assert "booked" in subject.lower() and "Tuesday Nov 10 at 3:30 PM" in subject and "booked a call-back reminder" in body


# ── 30 business minutes ─────────────────────────────────────────────────────
def at(day, hh, mm=0):
    return datetime(2026, 11, day, hh, mm, tzinfo=CT)


@pytest.mark.parametrize("received,now,due", [
    (at(9, 10, 0), at(9, 10, 29), False), (at(9, 10, 0), at(9, 10, 31), True),         # Monday: 29 / 31 minutes
    (at(9, 16, 50), at(9, 17, 30), False),                                              # clock stops at 17:00 (20 min left)
    (at(9, 16, 50), at(10, 9, 15), False), (at(9, 16, 50), at(10, 9, 25), True),        # resumes next morning
    (at(7, 11, 0), at(7, 11, 45), False),                                               # Saturday: starts Monday 09:00
    (at(7, 11, 0), at(9, 9, 31), True), (at(9, 7, 0), at(9, 9, 20), False),             # before opening
])
def test_business_due(received, now, due):
    assert cf.business_due(received.astimezone(timezone.utc), now.astimezone(timezone.utc)) is due


# ── service: ledger, booking, email ─────────────────────────────────────────
@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[CallbackFollowup.__table__, StaffCallQuality.__table__])
    with engine.begin() as c:     # production columns written by delivery_sync with raw SQL (the ORM model has no external_id)
        c.execute(text("CREATE TABLE channel_events (id TEXT, contact_id TEXT, kind TEXT, channel TEXT, event_at TIMESTAMP)"))
        c.execute(text("CREATE TABLE inbound_messages (id TEXT, contact_id TEXT, channel TEXT, body TEXT, received_at TIMESTAMP, "
                       "external_id TEXT)"))
    with sessionmaker(bind=engine)() as s:
        yield s


class FakeGHL:
    def __init__(self, slots=None, book_error=None, shadow=False, email="lead@example.com"):
        self.slots = slots if slots is not None else []
        self.book_error, self.shadow, self.email = book_error, shadow, email
        self.booked, self.slot_calls = [], 0

    def get_free_slots(self, cal, d0, d1, tz, user_id=None):
        self.slot_calls += 1
        if isinstance(self.slots, Exception):
            raise self.slots
        return self.slots

    def create_appointment(self, **kw):
        if self.book_error:
            raise self.book_error
        self.booked.append(kw)
        return {"shadow": True} if self.shadow else {"id": "APPT1"}

    def get_contact(self, cid):
        return {"contact": {"firstName": "Jacqulyn", "lastName": "T", "phone": "+19728007667", "email": self.email}}

    def close(self):
        pass


def run(session, ghl, *, enabled=True, req="", clear=False, kind="support", target="customer_support_bala", sid="call-1", live=True):
    sent = []
    cfg = {"callback_followup_enabled": "true" if enabled else "false"}
    flags = SimpleNamespace(ghl_writes_enabled=live)
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)), \
            patch("app.core.mode_flags.get_mode_flags", return_value=flags):
        row = svc.follow_up(session, SimpleNamespace(alert_email_to="kes@colaberry.com"), source="call", source_id=sid, contact_id="c1",
                            kind=kind, target_name=target, callback_wanted=True, time_is_clear=clear, requested_local=req,
                            summary="Wants to speak to someone.", flag_key="callback_followup_enabled", ghl=ghl, now=NOW,
                            send=lambda to, subj, body, cc: sent.append((to, subj, body, cc)))
    session.commit()
    return row, sent


def test_clear_time_free_slot_books_once_emails_staff_and_a_retry_does_nothing(session):
    ghl = FakeGHL(slots=[slot(10, 15, 30)])
    row, sent = run(session, ghl, req="2026-11-10T15:30", clear=True)
    assert row.action == "book" and row.status == "done" and row.appointment_id == "APPT1" and row.staff_key == "balakrishna"
    assert len(ghl.booked) == 1 and ghl.booked[0]["assigned_user_id"] == "yIhCTptvoNLixaWkLcRd"
    assert ghl.booked[0]["calendar_id"] == "CM6G3RwgWLrn4ULvXZLr"
    assert len(sent) == 1 and sent[0][0] == ["balakrishna.k@colaberry.com"] and "booked" in sent[0][1].lower()
    row2, sent2 = run(session, ghl, req="2026-11-10T15:30", clear=True)             # same call again
    assert row2 is None and sent2 == [] and len(ghl.booked) == 1
    assert len(session.execute(select(CallbackFollowup)).scalars().all()) == 1


def test_no_time_means_no_booking_and_one_staff_email(session):                    # call e5b7413f: "today, as soon as possible"
    ghl = FakeGHL()
    row, sent = run(session, ghl, req="", clear=False, kind="admissions", target="")
    assert row.action == "email_staff" and row.reason == "no_time" and ghl.booked == [] and ghl.slot_calls == 0
    assert len(sent) == 1 and sent[0][0] == ["roselen@colaberry.com"] and sent[0][3] == ["kes@colaberry.com"]


def test_taken_slot_unreadable_calendar_and_failed_booking_all_fall_back_to_the_staff_email(session):
    row, sent = run(session, FakeGHL(slots=[slot(10, 16, 0)]), req="2026-11-10T15:30", clear=True, sid="a")
    assert row.reason == "slot_taken" and len(sent) == 1
    row, sent = run(session, FakeGHL(slots=RuntimeError("401")), req="2026-11-10T15:30", clear=True, sid="b")
    assert row.reason == "calendar_unreadable" and len(sent) == 1
    ghl = FakeGHL(slots=[slot(10, 15, 30)], book_error=RuntimeError("422"))
    row, sent = run(session, ghl, req="2026-11-10T15:30", clear=True, sid="c")
    assert row.action == "email_staff" and row.reason == "booking_failed" and len(sent) == 1 and "refused" in sent[0][2]


def test_a_shadow_write_is_never_counted_as_booked(session):
    ghl = FakeGHL(slots=[slot(10, 15, 30)], shadow=True)
    row, sent = run(session, ghl, req="2026-11-10T15:30", clear=True, live=False)
    assert row.appointment_id is None and row.reason == "booking_failed" and len(sent) == 1


def test_flag_off_records_a_dry_run_and_does_nothing(session):
    ghl = FakeGHL(slots=[slot(10, 15, 30)])
    row, sent = run(session, ghl, enabled=False, req="2026-11-10T15:30", clear=True)
    assert row.status == "dry_run" and row.action == "book" and ghl.booked == []
    assert [x[0] for x in sent] == [["kes@colaberry.com"]]                          # only the notice to Kes, nothing to staff


def test_unknown_support_target_with_no_roster_emails_all_support_and_does_not_book(session):
    ghl = FakeGHL(slots=[slot(10, 15, 30)])
    row, sent = run(session, ghl, req="2026-11-10T15:30", clear=True, target="customer_support_nobody")
    assert ghl.booked == [] and row.reason == "no_calendar" and len(sent) == 1 and len(sent[0][0]) == 3


def test_unknown_support_target_uses_the_roster_from_settings_and_can_book_for_the_person_on_shift(session):
    import json

    ghl = FakeGHL(slots=[slot(10, 15, 30)])
    sent = []
    cfg = {"callback_followup_enabled": "true", "support_staff_roster": json.dumps(ROSTER)}
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)),             patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=True)):
        row = svc.follow_up(session, SimpleNamespace(alert_email_to=""), source="call", source_id="duty", contact_id="c1", kind="support",
                            target_name="customer_support_nobody", callback_wanted=True, time_is_clear=True,
                            requested_local="2026-11-10T15:30", summary="s", flag_key="callback_followup_enabled", ghl=ghl, now=NOW,
                            send=lambda to, subj, body, cc: sent.append(to))
    assert row.staff_key == "farhat" and row.action == "book" and ghl.booked[0]["calendar_id"] == "LdZxL3IggIMHAoCUYIvL"
    assert sent == [["farhat@colaberry.com"]]


def test_a_failed_email_send_marks_the_row_failed_but_never_raises(session):
    def boom(to, subj, body, cc):
        raise RuntimeError("smtp down")

    cfg = {"callback_followup_enabled": "true"}
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)):
        row = svc.follow_up(session, SimpleNamespace(alert_email_to=""), source="call", source_id="x", contact_id="c1", kind="admissions",
                            target_name="", callback_wanted=True, time_is_clear=False, requested_local="", summary="s",
                            flag_key="callback_followup_enabled", ghl=FakeGHL(), now=NOW, send=boom)
    assert row.status == "failed"


# ── process_call gating ─────────────────────────────────────────────────────
def _call(actions, intent="human_transfer_request"):
    return SimpleNamespace(call_id="e5b7413f", id="ce1", detected_intent=intent, lead_name="Jacqulyn", campaign_name="New Lead",
                           raw_payload_json={"executed_actions": actions})


FAILED = {"customer_support_bala": {"name": "customer_support_bala", "action_type": "transfer_call_action_type",
                                    "return_value": '{"failed_reason": "transfer_outside_availability", "status": "transfer-failed-connection-error"}',
                                    "error_message": "error"}}


def test_process_call_acts_only_on_a_failed_transfer_with_no_booking(session):
    cfg = {"callback_followup_enabled": "false"}
    analysis = SimpleNamespace(purpose_summary="Wants a person", requested_day="", requested_clock="", time_is_clear=False,
                               call_detailed_summary="")
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)):
        row = svc.process_call(session, SimpleNamespace(alert_email_to=""), _call(FAILED), analysis, contact_id="c1",
                               contact_phone="+1", ghl=FakeGHL(), now=NOW)
        none = svc.process_call(session, SimpleNamespace(alert_email_to=""), _call({}, intent="interested"), analysis, contact_id="c1",
                                contact_phone="+1", ghl=FakeGHL(), now=NOW)
    assert row is not None and row.source_id == "e5b7413f" and row.staff_key == "balakrishna" and row.status == "dry_run"
    assert none is None


# ── unanswered lead emails ──────────────────────────────────────────────────
def add_inbound(session, ext, minutes_ago, contact="c1", body="Can someone call me about the program?", at=None):
    session.execute(text("INSERT INTO inbound_messages VALUES (:i, :c, 'email', :b, :t, :e)"),
                    {"i": f"i-{ext}", "c": contact, "b": body, "t": at or NOW - timedelta(minutes=minutes_ago), "e": ext})
    session.commit()


def run_emails(session, ghl=None, analyze=None, enabled=True, now=NOW, force=True, every=None):
    sent = []
    cfg = {"unanswered_email_enabled": "true" if enabled else "false"}
    if every:
        cfg["unanswered_email_check_minutes"] = str(every)
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)), \
            patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=True)):
        out = svc.run_unanswered_email_check(session, SimpleNamespace(alert_email_to=""), now, ghl=ghl or FakeGHL(),
                                             analyze=analyze or (lambda b: {"purpose_summary": "Asks for a call.", "callback_wanted": "yes"}),
                                             send=lambda to, subj, body, cc: sent.append((to, subj)), force=force)
    session.commit()
    return out, sent


def test_email_unanswered_for_over_30_business_minutes_alerts_rose_once(session):
    add_inbound(session, "m1", 45)                         # Monday 15:30 Central, now 16:15
    out, sent = run_emails(session)
    assert out["handled"] == 1 and sent == [(["roselen@colaberry.com"], "Please call back: Jacqulyn T")]
    out2, sent2 = run_emails(session)                      # next hour: no duplicate
    assert out2["handled"] == 0 and sent2 == []


def test_the_email_check_only_works_every_30_minutes_not_every_minute(session):
    add_inbound(session, "m1", 45)
    for minute, works in [(0, True), (1, True), (2, False), (15, False), (29, False), (30, True), (31, True), (45, False)]:
        now = datetime(2026, 11, 9, 22, minute, tzinfo=timezone.utc)
        out, _ = run_emails(session, now=now, force=False, enabled=False)       # dry run: only the throttle is under test
        assert out["throttled"] is (not works), minute
    out, _ = run_emails(session, now=datetime(2026, 11, 9, 22, 10, tzinfo=timezone.utc), force=False, every=10)
    assert out["throttled"] is False                                              # the interval is configurable


def test_email_under_30_minutes_or_already_answered_is_left_alone(session):
    add_inbound(session, "recent", 20)
    add_inbound(session, "answered", 60, contact="c2")
    session.execute(text("INSERT INTO channel_events VALUES ('e1','c2','delivery','email', :t)"), {"t": NOW - timedelta(minutes=40)})
    session.commit()
    out, sent = run_emails(session)
    assert out["handled"] == 0 and sent == []


def test_email_outside_business_hours_waits_for_the_next_opening(session):
    sat = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)            # Saturday noon Central
    add_inbound(session, "sat", 0, body="Please call me", at=sat)
    out, _ = run_emails(session, now=datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc))          # Sunday: not due
    assert out["handled"] == 0
    out, sent = run_emails(session, now=datetime(2026, 11, 9, 15, 45, tzinfo=timezone.utc))      # Monday 09:45 Central: due
    assert out["handled"] == 1 and len(sent) == 1


def test_staff_writing_to_staff_is_not_an_unanswered_lead_email(session):
    add_inbound(session, "m1", 45)
    out, sent = run_emails(session, ghl=FakeGHL(email="aleem@colaberry.com"))
    assert out["skipped_internal"] == 1 and sent == []


def test_email_with_a_clear_free_time_books_for_rose(session):
    add_inbound(session, "m1", 45, body="Please call me tomorrow at 3:30 pm")
    ghl = FakeGHL(slots=[slot(10, 15, 30)])
    out, sent = run_emails(session, ghl=ghl, analyze=lambda b: {"callback_wanted": "yes", "time_is_clear": "yes", "requested_day": "tomorrow",
                                                              "requested_clock": "15:30", "purpose_summary": "x"})
    assert len(ghl.booked) == 1 and ghl.booked[0]["assigned_user_id"] == "0swBv9tBNeXeYXPYFBSx" and "booked" in sent[0][1].lower()


def test_a_failed_email_analysis_still_alerts_staff_with_the_start_of_the_email(session):
    add_inbound(session, "m1", 45, body="Hello, I need help with my payment plan")
    out, sent = run_emails(session, analyze=lambda b: {})
    assert out["handled"] == 1 and len(sent) == 1


def test_email_flag_off_is_a_dry_run(session):
    add_inbound(session, "m1", 45)
    out, sent = run_emails(session, enabled=False)
    assert sent == [] and session.execute(select(CallbackFollowup)).scalars().one().status == "dry_run"


def test_analyze_email_failure_returns_empty_and_never_raises():
    c = MagicMock()
    c.chat_completion.side_effect = RuntimeError("boom")
    assert svc.analyze_email(SimpleNamespace(), "hello", NOW, _client=c) == {}


# ── analysis fields ─────────────────────────────────────────────────────────
def test_call_analysis_carries_the_callback_fields_and_defaults_safely():
    from app.core.ai_message_generator import generate_ghl_call_analysis

    client = MagicMock()
    client.chat_completion.return_value = {
        "task_title": "t", "task_description": "d", "assign_to": "", "is_lead_classification": True, "lead_classification": "warm_lead",
        "create_task": "yes", "outbound_call_details": "", "call_detailed_summary": "", "ai_campaign": "Yes",
        "call_start_time_formatted": "", "task_due_date": "", "callback_wanted": "yes", "requested_day": "Tomorrow",
        "requested_clock": "15:30", "time_is_clear": "yes", "purpose_summary": "Wants admissions."}
    with patch("app.adapters.openai_client.OpenAIClient", return_value=client):
        r = generate_ghl_call_analysis("bot: hi", 1, 60, "+1", SimpleNamespace(), None)
        assert r.callback_wanted and r.time_is_clear and (r.requested_day, r.requested_clock) == ("tomorrow", "15:30")
        assert r.purpose_summary == "Wants admissions."
        client.chat_completion.return_value = {"task_title": "t"}                  # older / partial answer: safe defaults
        r2 = generate_ghl_call_analysis("bot: hi", 1, 60, "+1", SimpleNamespace(), None)
        assert (r2.callback_wanted, r2.time_is_clear, r2.requested_day, r2.requested_clock) == (False, False, "", "")


# ── shadow week: pending rows, approve / reject, contacted, dashboard numbers ────────────────────────────────────────────────
def shadow(session, ghl=None, **kw):
    return run(session, ghl or FakeGHL(slots=[slot(10, 15, 30)]), enabled=False, **kw)


def approve(session, row_id, decision="approve", ghl=None, now=NOW):
    sent = []
    cfg = {}
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)),             patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=True)):
        out = svc.decide_row(session, SimpleNamespace(alert_email_to="kes@colaberry.com"), row_id, decision, "kes", ghl=ghl or FakeGHL(slots=[slot(10, 15, 30)]),
                             send=lambda to, subj, body, cc: sent.append((to, subj)), now=now)
    session.commit()
    return out, sent


def test_a_shadow_row_records_the_lead_staff_and_summary_and_waits_for_approval(session):
    row, sent = shadow(session, req="2026-11-10T15:30", clear=True)
    assert len(sent) == 1 and sent[0][0] == ["kes@colaberry.com"] and row.status == "dry_run" and row.approval_status == "pending"
    assert (row.lead_name, row.lead_phone, row.lead_email) == ("Jacqulyn T", "+19728007667", "lead@example.com")
    assert row.staff_name == "Balakrishna K" and row.summary == "Wants to speak to someone." and row.plan_start.startswith("2026-11-10T15:30")


def test_approving_a_booking_row_books_and_emails_once(session):
    row, _ = shadow(session, req="2026-11-10T15:30", clear=True)
    ghl = FakeGHL(slots=[slot(10, 15, 30)])
    out, sent = approve(session, row.id, ghl=ghl)
    assert out == {"ok": True, "status": "done", "action": "book"} and len(ghl.booked) == 1 and len(sent) == 1
    session.refresh(row)
    assert row.approval_status == "approved" and row.status == "done" and row.appointment_id == "APPT1" and row.decided_by == "kes"
    again, sent2 = approve(session, row.id, ghl=ghl)                       # a double click
    assert again["ok"] is False and len(ghl.booked) == 1 and sent2 == []


def test_approval_rechecks_the_slot_and_falls_back_to_the_staff_email(session):
    row, _ = shadow(session, req="2026-11-10T15:30", clear=True)
    ghl = FakeGHL(slots=[slot(10, 16, 0)])                                  # taken since the shadow decision
    out, sent = approve(session, row.id, ghl=ghl)
    assert out["action"] == "email_staff" and ghl.booked == [] and len(sent) == 1
    session.refresh(row)
    assert row.reason == "slot_taken"
    row2, _ = shadow(session, req="2026-11-10T15:30", clear=True, sid="late")
    out, _ = approve(session, row2.id, ghl=FakeGHL(slots=[slot(10, 15, 30)]), now=NOW + timedelta(days=3))   # approved after the time passed
    assert out["action"] == "email_staff"


def test_approving_an_email_only_row_sends_the_staff_email(session):
    row, _ = shadow(session, req="", clear=False, kind="admissions", target="")
    out, sent = approve(session, row.id)
    assert out["status"] == "done" and sent == [(["roselen@colaberry.com"], "Please call back: Jacqulyn T")]


def test_rejecting_closes_the_row_and_sends_nothing(session):
    row, _ = shadow(session, req="", clear=False)
    ghl = FakeGHL()
    out, sent = approve(session, row.id, decision="reject", ghl=ghl)
    session.refresh(row)
    assert out["status"] == "rejected" and sent == [] and row.approval_status == "rejected" and row.status == "dry_run"
    assert approve(session, row.id)[0]["ok"] is False                       # a rejected row cannot be approved later
    assert svc.decide_row(session, SimpleNamespace(), "nope", "approve", "kes")["ok"] is False
    assert svc.decide_row(session, SimpleNamespace(), row.id, "maybe", "kes")["ok"] is False


def test_contacted_is_a_connected_staff_call_after_the_request(session):
    row, _ = shadow(session, req="", clear=False)
    created = datetime(2026, 11, 9, 22, 20, tzinfo=timezone.utc)
    row.created_at = created
    session.add_all([
        StaffCallQuality(id="q0", ghl_message_id="gm-q0", ghl_contact_id="c1", call_time=created - timedelta(hours=1), call_connected=True),
        StaffCallQuality(id="q1", ghl_message_id="gm-q1", ghl_contact_id="c1", call_time=created + timedelta(hours=1), call_connected=False),
    ])
    session.commit()
    assert svc.update_contacted(session, SimpleNamespace(), NOW + timedelta(hours=3), force=True) == 0     # only an old call + a no-answer
    session.add(StaffCallQuality(id="q2", ghl_message_id="gm-q2", ghl_contact_id="c1", call_time=created + timedelta(hours=2),
                                 call_connected=True))
    session.commit()
    assert svc.update_contacted(session, SimpleNamespace(), NOW + timedelta(hours=3), force=True) == 1
    session.commit()
    session.refresh(row)
    assert row.contacted_at is not None
    assert svc.update_contacted(session, SimpleNamespace(), NOW, force=False) == 0                          # throttled outside :00-:01 / :30-:31


def test_dashboard_summary_counts_and_lead_list(session):
    shadow(session, req="2026-11-10T15:30", clear=True, sid="a")                          # planned booking
    r2, _ = shadow(session, req="", clear=False, sid="b")                                  # planned email
    r3, _ = shadow(session, req="", clear=False, sid="c")
    approve(session, r2.id)
    approve(session, r3.id, decision="reject")
    cfg = {}
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)):
        out = svc.get_summary(session, SimpleNamespace())
    c = out["counts"]
    assert c["requests"] == 3 and c["booking_planned"] == 1 and c["email_planned"] == 2 and c["from_calls"] == 3
    assert (c["pending_approval"], c["approved"], c["rejected"], c["staff_notified"], c["booked"]) == (1, 1, 1, 1, 0)
    assert out["calls_enabled"] is False and out["emails_enabled"] is False
    top = out["leads"][0]
    assert {"lead_name", "lead_email", "lead_phone", "staff_name", "summary", "approval_status", "action"} <= set(top)


# ── Kes is emailed when a new request lands on the card (shadow week) ───────────────────────────────────────────────────────
def test_a_new_shadow_row_emails_the_alert_address_once_with_the_lead_details(session):
    row, sent = shadow(session, req="2026-11-10T15:30", clear=True)
    assert len(sent) == 1                                          # the pending notice; no staff email, no booking
    to, subject, body, cc = sent[0]
    assert to == ["kes@colaberry.com"] and subject == "[Cora] Call-back request waiting for approval: Jacqulyn T"
    for must in ("waiting for your approval", "nothing has been sent", "+19728007667", "lead@example.com", "Balakrishna K",
                 "Wants to speak to someone.", "book a call-back on Balakrishna K's calendar", "Call-back Follow-ups"):
        assert must in body
    _, again = shadow(session, req="2026-11-10T15:30", clear=True)             # replay of the same call: no second notice
    assert again == []


def test_the_pending_notice_can_be_switched_off_and_never_blocks_the_row(session):
    sent = []
    cfg = {"callback_followup_enabled": "false", "callback_followup_notify_pending": "false"}
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)):
        row = svc.follow_up(session, SimpleNamespace(alert_email_to="kes@colaberry.com"), source="call", source_id="off", contact_id="c1",
                            kind="admissions", target_name="", callback_wanted=True, time_is_clear=False, requested_local="", summary="s",
                            flag_key="callback_followup_enabled", ghl=FakeGHL(), now=NOW, send=lambda *a: sent.append(a))
    assert row.approval_status == "pending" and sent == []

    def boom(*a):
        raise RuntimeError("smtp down")

    cfg = {"callback_followup_enabled": "false"}
    with patch.object(svc, "_cfg", lambda s, st, key, default: cfg.get(key, default)):
        row = svc.follow_up(session, SimpleNamespace(alert_email_to="kes@colaberry.com"), source="call", source_id="boom", contact_id="c1",
                            kind="admissions", target_name="", callback_wanted=True, time_is_clear=False, requested_local="", summary="s",
                            flag_key="callback_followup_enabled", ghl=FakeGHL(), now=NOW, send=boom)
    assert row is not None and row.approval_status == "pending"                # a mail failure never loses the request
