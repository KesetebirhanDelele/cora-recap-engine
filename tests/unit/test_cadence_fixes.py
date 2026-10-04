"""
Tests for the 2026-10-01 cadence fixes (spec/33): slot rebalancer never pulls calls earlier,
hard daily call cap (2/lead/local day, callbacks exempt), real-SMS routing, TCPA hours for SMS.
SQLite + pure logic; no wall-clock dependence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.call_policy import (
    CALLBACK_INTENT_REASONS,
    is_lead_requested_callback,
    local_day_start_utc,
    next_local_day_start_utc,
)
from app.core.followup_routing import build_followup_updates as _build, normalise_sms_mode
from app.models import Base, ScheduledJob
from app.models.app_config import AppConfig  # noqa: F401  (registers the table before create_all)
from app.worker.jobs.outbound_jobs import calls_launched_today, daily_cap_deferral
from app.worker.jobs.slot_rebalancer import plan_redistribution, redistribute_overages

UTC = timezone.utc
CHI = "America/Chicago"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _job(session, *, run_at, status="pending", entity="lead-1", payload=None, updated_at=None, version=0):
    now = datetime.now(tz=UTC)
    j = ScheduledJob(
        id=str(uuid.uuid4()), job_type="launch_outbound_call", entity_type="lead", entity_id=entity,
        run_at=run_at, status=status, payload_json=payload if payload is not None else {"contact_id": entity},
        version=version, created_at=now, updated_at=updated_at or now,
    )
    session.add(j)
    session.flush()
    return j


# ═════════════════════════════ slot rebalancer ═══════════════════════════════

SLOT = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)          # slot-aligned, far future


def test_planner_leaves_in_capacity_slots_alone():
    rows = [(f"j{i}", SLOT + timedelta(seconds=75 * i), False) for i in range(4)]
    assert plan_redistribution(rows) == []


def test_planner_far_future_jobs_are_never_pulled_forward():
    """The 2026-09/10 bug: one over-full slot used to repack EVERY pending call from 'now'."""
    near = [(f"n{i}", SLOT + timedelta(seconds=10 * i), False) for i in range(6)]             # one slot, 6 jobs
    far = [(f"f{i}", SLOT + timedelta(hours=48, minutes=5 * i), False) for i in range(10)]    # 48 h out
    moves = dict(plan_redistribution(near + far))
    assert not any(k.startswith("f") for k in moves)            # 48h jobs untouched
    assert len(moves) == 2                                       # only the 2 excess near jobs move


def test_planner_only_the_excess_moves_and_always_later():
    rows = [(f"j{i}", SLOT + timedelta(seconds=i), False) for i in range(7)]
    moves = plan_redistribution(rows)
    assert len(moves) == 3
    originals = {r[0]: r[1] for r in rows}
    for job_id, new in moves:
        assert new > originals[job_id]
        assert new >= SLOT + timedelta(seconds=300)              # next slot or later


def test_planner_spreads_excess_across_following_slots_with_75s_spacing():
    rows = [(f"j{i}", SLOT + timedelta(seconds=i), False) for i in range(10)]
    moves = sorted(plan_redistribution(rows), key=lambda m: m[1])
    assert [m[1] for m in moves] == [SLOT + timedelta(seconds=300 + 75 * k) for k in range(4)] + \
           [SLOT + timedelta(seconds=600 + 75 * k) for k in range(2)]


def test_planner_immovable_callbacks_hold_their_slot_and_others_route_around():
    callbacks = [(f"cb{i}", SLOT + timedelta(seconds=7 * i), True) for i in range(4)]       # fill the slot
    normal = [("n0", SLOT + timedelta(seconds=40), False)]
    moves = dict(plan_redistribution(callbacks + normal))
    assert "cb0" not in moves and not any(k.startswith("cb") for k in moves)
    assert moves["n0"] >= SLOT + timedelta(seconds=300)


def test_planner_is_idempotent():
    rows = [(f"j{i}", SLOT + timedelta(seconds=i), False) for i in range(9)]
    moved = dict(plan_redistribution(rows))
    after = [(job_id, moved.get(job_id, run_at), False) for job_id, run_at, _ in rows]
    assert plan_redistribution(after) == []


def test_redistribute_overages_updates_db_never_earlier_and_skips_non_pending(session):
    base = datetime.now(tz=UTC).replace(microsecond=0) + timedelta(days=2)
    base = base - timedelta(seconds=int(base.timestamp()) % 300)
    over = [_job(session, run_at=base + timedelta(seconds=i), entity=f"o{i}") for i in range(6)]
    far = _job(session, run_at=base + timedelta(hours=40), entity="far")
    claimed = _job(session, run_at=base + timedelta(seconds=3), status="claimed", entity="claimed")
    cb = _job(session, run_at=base + timedelta(seconds=9), entity="cb",
              payload={"contact_id": "cb", "intent_reason": "callback_with_time"})
    before = {j.id: _aware(j.run_at) for j in over + [far, claimed, cb]}

    moved = redistribute_overages(session)
    session.flush()
    for j in over + [far, claimed, cb]:
        session.refresh(j)
    assert moved >= 1
    assert all(_aware(j.run_at) >= before[j.id] for j in over + [far, claimed, cb])           # never earlier
    assert _aware(far.run_at) == before[far.id]                                               # 40 h out: untouched
    assert _aware(claimed.run_at) == before[claimed.id] and _aware(cb.run_at) == before[cb.id]
    assert redistribute_overages(session) == 0                                                # idempotent


# ═══════════════════════════════ daily call cap ══════════════════════════════

def _settings():
    return SimpleNamespace(default_timezone=CHI)


NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)           # Wed 13:00 Chicago


def _done(session, when, entity="lead-1", payload=None, status="completed"):
    return _job(session, run_at=when, status=status, entity=entity, updated_at=when, payload=payload)


def _cap(session, job, payload=None, *, now=NOW, entity="lead-1", campaign="Cold Lead"):
    return daily_cap_deferral(
        session, _settings(), job=job, payload=payload if payload is not None else {"contact_id": entity},
        contact_id=entity, phone="", campaign_name=campaign, contact_tz=CHI, now=now,
    )


def test_under_cap_proceeds(session):
    _done(session, NOW - timedelta(hours=3), entity="u1")
    cur = _job(session, run_at=NOW, entity="u1")
    assert _cap(session, cur, entity="u1") is None


def test_at_cap_defers_to_next_local_day_inside_the_window(session):
    _done(session, NOW - timedelta(hours=4), entity="c1")
    _done(session, NOW - timedelta(hours=1), entity="c1")
    cur = _job(session, run_at=NOW, entity="c1")
    run_at = _cap(session, cur, entity="c1")
    assert run_at is not None
    local = run_at.astimezone(ZoneInfo(CHI))
    assert local.date() == (NOW.astimezone(ZoneInfo(CHI)) + timedelta(days=1)).date()
    assert 9 <= local.hour < 17 and local.weekday() < 5          # Cold Lead window, next day


def test_lead_requested_callback_is_exempt(session):
    for hours in (4, 1):
        _done(session, NOW - timedelta(hours=hours), entity="cb1")
    for reason in sorted(CALLBACK_INTENT_REASONS):
        cur = _job(session, run_at=NOW, entity="cb1", payload={"contact_id": "cb1", "intent_reason": reason})
        assert _cap(session, cur, payload=cur.payload_json, entity="cb1") is None
    assert is_lead_requested_callback({"intent_reason": "callback_request"})
    assert not is_lead_requested_callback({"intent_reason": "booking_retry"})
    assert not is_lead_requested_callback({"source": "campaign_entry:cold_lead"})


def test_other_days_cancelled_and_failed_calls_do_not_count(session):
    _done(session, NOW - timedelta(days=1, hours=2), entity="d1")                      # yesterday
    _done(session, NOW - timedelta(hours=2), entity="d1", status="cancelled")
    _done(session, NOW - timedelta(hours=1), entity="d1", status="failed")
    cur = _job(session, run_at=NOW, entity="d1")
    assert _cap(session, cur, entity="d1") is None


def test_day_boundary_is_the_leads_local_midnight(session):
    # 04:30 UTC on Oct 7 = 23:30 Chicago on Oct 6 -> belongs to YESTERDAY for a Chicago lead
    _done(session, datetime(2026, 10, 7, 4, 30, tzinfo=UTC), entity="t1")
    _done(session, datetime(2026, 10, 7, 4, 40, tzinfo=UTC), entity="t1")
    cur = _job(session, run_at=NOW, entity="t1")
    assert _cap(session, cur, entity="t1") is None
    assert local_day_start_utc(NOW, CHI) == datetime(2026, 10, 7, 5, 0, tzinfo=UTC)
    assert next_local_day_start_utc(NOW, CHI) == datetime(2026, 10, 8, 5, 0, tzinfo=UTC)


def test_counts_calls_matched_by_entity_id_or_payload_contact_id_and_excludes_self(session):
    a = _done(session, NOW - timedelta(hours=3), entity="phone-1", payload={"contact_id": "ghl-1"})
    _done(session, NOW - timedelta(hours=2), entity="ghl-1")
    day = local_day_start_utc(NOW, CHI)
    assert calls_launched_today(session, "ghl-1", "phone-1", day) == 2
    assert calls_launched_today(session, "ghl-1", "phone-1", day, exclude_job_id=a.id) == 1


def test_cap_is_configurable_and_zero_disables(session):
    for hours in (5, 3, 1):
        _done(session, NOW - timedelta(hours=hours), entity="cfg1")
    cur = _job(session, run_at=NOW, entity="cfg1")
    assert _cap(session, cur, entity="cfg1") is not None                       # 3 calls >= default 2
    session.add(AppConfig(key="max_calls_per_lead_per_day", value="4", updated_by="t"))
    session.flush()
    assert _cap(session, cur, entity="cfg1") is None                           # 3 < 4
    session.get(AppConfig, "max_calls_per_lead_per_day").value = "0"
    session.flush()
    assert _cap(session, cur, entity="cfg1") is None                           # 0 = disabled


# ═════════════════════════════ follow-up routing ═════════════════════════════


def build_followup_updates(*a, **kw):
    plan = _build(*a, **kw)
    return plan.updates, plan.sms_skip_reason


CFG = SimpleNamespace(
    ghl_field_mark_as_lead="Mark as Lead", ghl_field_ai_campaign="AI Campaign",
    ghl_field_message="Message:", ghl_field_support_ticket_2="Support Issue Ticket #2",
    ghl_field_support_ticket_4="Support issue Ticket #4",
)
CLEAN = {"contact": {"id": "c", "phone": "+15551230000", "email": "a@b.test", "dnd": False,
                     "dndSettings": {}, "tags": []}}


def test_email_followup_writes_subject_to_ticket2_and_body_to_message_only():
    u, skip = build_followup_updates(CFG, channel="email", subject="Subj", body="<p>Body</p>", ghl_contact=CLEAN)
    assert skip is None
    assert u["Support Issue Ticket #2"] == "Subj" and u["Message:"] == "<p>Body</p>"
    assert "Support issue Ticket #4" not in u


def test_sms_followup_is_a_real_sms_via_ticket4_and_never_touches_ticket2():
    u, skip = build_followup_updates(CFG, channel="sms", subject="", body="Hi, it's Cora. Text STOP to stop alerts",
                                     ghl_contact=CLEAN)
    assert skip is None
    assert u["Support issue Ticket #4"].startswith("Hi, it's Cora")
    assert "Support Issue Ticket #2" not in u                    # the email workflow must NOT fire


def test_classification_is_never_written_to_ticket4():
    for channel in ("email", "sms"):
        u, _ = build_followup_updates(CFG, channel=channel, subject="s", body="hello", ghl_contact=CLEAN)
        assert "warm_lead" not in u.values()
        if channel == "email":
            assert "Support issue Ticket #4" not in u          # nothing at all for the SMS workflow


@pytest.mark.parametrize("record,expect", [
    ({"contact": {"phone": "+1", "dnd": True}}, "all channels"),
    ({"contact": {"phone": "+1", "dndSettings": {"SMS": {"status": "active"}}}}, "SMS"),
    ({"contact": {"phone": "+1", "tags": ["Unsubscribed"]}}, "tagged"),
    ({"contact": {"email": "a@b.test"}}, "no phone"),
    ({}, "could not be read"),
    (None, "could not be read"),
])
def test_sms_is_skipped_for_dnd_optout_no_phone_or_unreadable_contact(record, expect):
    u, skip = build_followup_updates(CFG, channel="sms", subject="", body="text", ghl_contact=record)
    assert skip and expect in skip
    assert "Support issue Ticket #4" not in u
    assert u["Mark as Lead"] == "Yes"                            # the rest of the update still happens


def test_email_only_dnd_does_not_block_an_sms():
    rec = {"contact": {"phone": "+1", "dndSettings": {"Email": {"status": "active"}}}}
    u, skip = build_followup_updates(CFG, channel="sms", subject="", body="text", ghl_contact=rec)
    assert skip is None and "Support issue Ticket #4" in u


def test_legacy_mode_restores_sms_text_via_ticket2_and_truncates():
    u, skip = build_followup_updates(CFG, channel="sms", subject="", body="x" * 500, ghl_contact=CLEAN, sms_mode="email")
    assert skip is None and len(u["Support Issue Ticket #2"]) == 200
    assert "Support issue Ticket #4" not in u
    assert normalise_sms_mode("EMAIL ") == "email" and normalise_sms_mode(None) == "sms" and normalise_sms_mode("sms") == "sms"


def test_sms_skipped_when_ticket4_not_configured():
    cfg = SimpleNamespace(**{**vars(CFG), "ghl_field_support_ticket_4": None})
    _, skip = build_followup_updates(cfg, channel="sms", subject="", body="text", ghl_contact=CLEAN)
    assert skip and "TICKET_4" in skip


# ═══════════════════════════ SMS TCPA hours (channel jobs) ═══════════════════

def _sms_job(session, contact_id):
    now = datetime.now(tz=UTC)
    j = ScheduledJob(id=str(uuid.uuid4()), job_type="send_sms", entity_type="lead", entity_id=contact_id,
                     run_at=now, status="claimed", payload_json={"contact_id": contact_id, "campaign_name": "New Lead"},
                     version=0, created_at=now, updated_at=now)
    session.add(j)
    session.flush()
    return j


def _window_settings():
    return SimpleNamespace(default_timezone=CHI)


def _pending_copy(session, contact_id):
    from sqlalchemy import select

    return session.scalars(select(ScheduledJob).where(
        ScheduledJob.job_type == "send_sms", ScheduledJob.entity_id == contact_id,
        ScheduledJob.status == "pending")).first()


def test_sms_is_deferred_after_9pm_local_even_when_the_campaign_window_is_open(session):
    from app.worker.jobs.channel_jobs import _check_active_window

    for k, v in {"new_lead_active_days": "0,1,2,3,4,5,6", "new_lead_active_start_hour": "8",
                 "new_lead_active_end_hour": "22"}.items():
        if session.get(AppConfig, k) is None:
            session.add(AppConfig(key=k, value=v, updated_by="t"))
    session.flush()
    cid = f"tcpa-{uuid.uuid4().hex[:5]}"
    at_2130 = datetime(2026, 10, 7, 21, 30, tzinfo=ZoneInfo(CHI)).astimezone(UTC)

    email_job = _sms_job(session, cid + "e")
    assert _check_active_window(session, email_job, cid + "e", "New Lead", _window_settings(), now=at_2130) is True   # email: fine

    sms_job = _sms_job(session, cid)
    assert _check_active_window(session, sms_job, cid, "New Lead", _window_settings(), tcpa=True, now=at_2130) is False
    nxt = _pending_copy(session, cid)
    local = _aware(nxt.run_at).astimezone(ZoneInfo(CHI))
    assert local.date() == datetime(2026, 10, 8).date() and 8 <= local.hour < 22


def test_sms_inside_tcpa_hours_proceeds(session):
    from app.worker.jobs.channel_jobs import _check_active_window

    cid = f"tcpa-ok-{uuid.uuid4().hex[:5]}"
    at_1400 = datetime(2026, 10, 7, 14, 0, tzinfo=ZoneInfo(CHI)).astimezone(UTC)
    job = _sms_job(session, cid)
    assert _check_active_window(session, job, cid, "New Lead", _window_settings(), tcpa=True, now=at_1400) is True


# ═══════════════════ links in SMS: only myfreeaiclass.com, else email ═══════════════════

from app.core.sms_links import (  # noqa: E402
    email_html_from_text,
    email_subject_from_text,
    extract_links,
    parse_allowed_domains,
    sms_needs_email,
)

NO_LINKS = parse_allowed_domains(None)                       # the default: no link of any kind in an SMS
ALLOW = parse_allowed_domains("myfreeaiclass.com")           # only when explicitly re-allowed in app_config


@pytest.mark.parametrize("text", [
    "Start free at myfreeaiclass.com. Text STOP to stop alerts",
    "Start free at www.myfreeaiclass.com",
    "Start free at https://www.myfreeaiclass.com/learn",
    "Email admissions@colaberry.com with questions",           # an email address is not a link
    "No links here at all.Thanks",
])
def test_sms_text_that_may_stay_an_sms_only_when_the_domain_is_explicitly_allowed(text):
    assert not sms_needs_email(text, ALLOW)


@pytest.mark.parametrize("text", [
    "Start free at myfreeaiclass.com. Text STOP to stop alerts",
    "Start free at www.myfreeaiclass.com",
    "Start free at https://www.myfreeaiclass.com/learn",
])
def test_by_default_not_even_our_own_website_may_be_in_an_sms(text):
    assert sms_needs_email(text, NO_LINKS)


def test_texts_without_any_link_or_with_an_email_address_stay_sms_by_default():
    assert not sms_needs_email("Hi, it's Cora. Reply YES for details. Text STOP to stop alerts", NO_LINKS)
    assert not sms_needs_email("Email admissions@colaberry.com with questions", NO_LINKS)


@pytest.mark.parametrize("text", [
    "RSVP: https://www.eventbrite.com/e/colaberry-open-house-123",
    "Watch https://www.youtube.com/watch?v=xLJcCCDCnis",
    "see bit.ly/abc123",
    "go to colaberry.com today",
    "visit www.example.org",
    "evil-myfreeaiclass.com.attacker.io",
    "Start free at myfreeaiclass.com and RSVP at eventbrite.com/e/1",   # one bad link is enough
])
def test_sms_text_with_any_other_link_must_go_by_email(text):
    assert sms_needs_email(text, ALLOW)


def test_default_and_empty_allow_lists_are_the_same_and_subdomains_of_allowed_pass():
    assert parse_allowed_domains(None) == parse_allowed_domains("") == frozenset()
    assert sms_needs_email("Start free at myfreeaiclass.com", parse_allowed_domains(""))
    assert not sms_needs_email("Start at learn.myfreeaiclass.com", ALLOW)
    assert parse_allowed_domains("WWW.MyFreeAIClass.com, colaberry.com") == frozenset({"myfreeaiclass.com", "colaberry.com"})


def test_link_extraction_trims_punctuation():
    assert extract_links("Go to https://a.example.com/x?y=1.") == ["https://a.example.com/x?y=1"]


def test_email_made_from_a_link_text_has_a_useful_subject_clickable_link_and_no_stop_line():
    text = "Hi there! It's Cora from Colaberry. RSVP for our Open House: https://www.eventbrite.com/e/x-1. Text STOP to stop alerts"
    subj = email_subject_from_text(text)
    assert subj == "It's Cora from Colaberry." and "http" not in subj
    html = email_html_from_text(text)
    assert '<a href="https://www.eventbrite.com/e/x-1">' in html and "STOP" not in html and html.startswith("<p>")
    assert email_subject_from_text("Hey! http://x.co/a") == "Hey!"                        # nothing better available
    assert email_subject_from_text("http://x.co/a") == "A quick note from Colaberry"      # nothing usable -> fallback
    assert len(email_subject_from_text("word " * 60)) <= 70


def test_sms_with_a_link_is_delivered_as_an_email_not_a_text():
    text = "Hi there! This is Cora from Colaberry. RSVP: https://www.eventbrite.com/e/x-1 Text STOP to stop alerts"
    plan = _build(CFG, channel="sms", subject="", body=text, ghl_contact=CLEAN)
    assert plan.route == "sms_link_to_email" and plan.sms_skip_reason is None
    assert "Support issue Ticket #4" not in plan.updates                       # NOT texted
    assert plan.updates["Support Issue Ticket #2"] == "This is Cora from Colaberry."   # starts the EMAIL workflow
    assert plan.updates["Message:"].startswith("<p>") and "eventbrite.com/e/x-1" in plan.updates["Message:"]


def test_sms_with_our_own_website_goes_by_email_by_default_and_stays_sms_only_if_allowed():
    body = "Start free at myfreeaiclass.com. Text STOP to stop alerts"
    plan = _build(CFG, channel="sms", subject="", body=body, ghl_contact=CLEAN)
    assert plan.route == "sms_link_to_email" and "Support issue Ticket #4" not in plan.updates
    ok = _build(CFG, channel="sms", subject="", body=body, ghl_contact=CLEAN, allowed_link_domains="myfreeaiclass.com")
    assert ok.route == "sms" and "Support issue Ticket #4" in ok.updates and "Support Issue Ticket #2" not in ok.updates


def test_a_plain_text_without_a_link_is_a_real_sms_by_default():
    plan = _build(CFG, channel="sms", subject="", body="Hi, it's Cora. Reply YES for details. Text STOP to stop alerts",
                  ghl_contact=CLEAN)
    assert plan.route == "sms" and plan.updates["Support issue Ticket #4"].startswith("Hi, it's Cora")


def test_link_text_to_a_lead_who_cannot_be_emailed_is_not_sent_at_all():
    no_email = {"contact": {"phone": "+15551230000", "dnd": False, "dndSettings": {}, "tags": []}}
    email_dnd = {"contact": {"email": "a@b.test", "phone": "+1", "dndSettings": {"Email": {"status": "active"}}}}
    for rec, why in ((no_email, "no email"), (email_dnd, "Email"), ({}, "could not be read")):
        plan = _build(CFG, channel="sms", subject="", body="RSVP https://eventbrite.com/e/1", ghl_contact=rec)
        assert plan.sms_skip_reason and why in plan.sms_skip_reason
        assert "Support issue Ticket #4" not in plan.updates and "Support Issue Ticket #2" not in plan.updates


def test_allow_list_is_configurable_per_call():
    body = "RSVP https://eventbrite.com/e/1"
    assert _build(CFG, channel="sms", subject="", body=body, ghl_contact=CLEAN,
                  allowed_link_domains="eventbrite.com").route == "sms"
    assert _build(CFG, channel="sms", subject="", body="Start free at myfreeaiclass.com", ghl_contact=CLEAN,
                  allowed_link_domains="").route == "sms_link_to_email"


# ═══════════ the no-link rule reaches the correction SMS and the AI prompt ═══════════

def test_the_correction_sms_text_contains_no_link_of_any_kind():
    from types import SimpleNamespace as NS

    from app.services import wrong_date_monitor as wdm

    class _S:  # stand-in session: every app_config lookup falls through to settings/defaults
        def get(self, *a, **k):
            return None

    settings = NS(next_class_start="November 12, 2026", next_open_house_date="October 29, 2026",
                  live_open_house_link="https://eventbrite.com/e/1", sender_name="Cora from Colaberry",
                  unsubscribe_text="Text STOP to stop alerts", free_signup_url="www.myfreeaiclass.com")
    sms = wdm.build_correction_text(_S(), settings, "sms")
    assert "November 12, 2026" in sms and "October 29, 2026" in sms and sms.endswith("Text STOP to stop alerts")
    assert not sms_needs_email(sms, NO_LINKS), sms
    assert extract_links(sms) == []
    email = wdm.build_correction_text(_S(), settings, "email")
    assert "eventbrite.com" in email                              # the email still carries the RSVP link


def test_prompt_override_keeps_the_free_signup_address_out_of_the_sms():
    from app.core.schedule_context import build_schedule_block

    block = build_schedule_block("", "", "https://rsvp.test", "www.myfreeaiclass.com")
    assert "ONLY in the email" in block and "NEVER put any link" in block


# ── email follow-ups obey the same GHL opt-out signals as texts (Ali, 2026-10-03) ──────────────────

def _rec(**over):
    c = {"id": "c", "phone": "+15551230000", "email": "a@b.test", "dnd": False, "dndSettings": {}, "tags": []}
    c.update(over)
    return {"contact": c}


@pytest.mark.parametrize("record,why", [
    (_rec(tags=["Do Not Contact"]), "tagged"),
    (_rec(tags=["unsubscribed"]), "tagged"),
    (_rec(dnd=True), "DND is on (all channels)"),
    (_rec(dndSettings={"Email": {"status": "active"}}), "DND is on for Email"),
    (_rec(email=""), "no email address"),
    ({}, "could not be read"),
])
def test_email_followup_is_withheld_when_ghl_says_stop_or_has_no_address(record, why):
    u, skip = build_followup_updates(CFG, channel="email", subject="Subj", body="<p>Body</p>", ghl_contact=record)
    assert skip and why in skip
    assert "Support Issue Ticket #2" not in u and "Message:" not in u        # no email workflow trigger is written
    assert u.get("Mark as Lead") == "Yes"                                       # bookkeeping fields are untouched


def test_other_channel_dnd_does_not_block_an_email():
    rec = _rec(dndSettings={"SMS": {"status": "active"}, "Call": {"status": "active"}}, tags=["not interested"])
    u, skip = build_followup_updates(CFG, channel="email", subject="Subj", body="<p>Body</p>", ghl_contact=rec)
    assert skip is None and u["Support Issue Ticket #2"] == "Subj"
