"""Opt-out handling (spec/36): wording classifier (pure) + DB-backed apply / review / undo / reconcile /
Cora-side block (opt-in WRONG_DATE_TEST_DATABASE_URL)."""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core import optout as oo

# ───────────────────────── wording → scope (pure) ─────────────────────────

CALL, SMS, EMAIL, ALL = oo.CALL, oo.SMS, oo.EMAIL, oo.ALL


@pytest.mark.parametrize("said,channel,scope", [
    ("STOP", "sms", {SMS}), ("stop.", "sms", {SMS}), ("Unsubscribe", "email", {EMAIL}), ("stopall", "sms", {SMS}),
    ("Please stop calling me", "call", {CALL}),
    ("don't call me again", "call", {CALL}),
    ("I said do not call", "call", {CALL}),
    ("Stop texting me!!", "sms", {SMS}),
    ("no more texts please", "sms", {SMS}),
    ("please stop emailing me", "email", {EMAIL}),
    ("don't send me any more emails", "email", {EMAIL}),
    ("don't call or text me", "call", {CALL, SMS}),
    ("stop messaging me", "sms", {SMS, EMAIL}),
    ("Remove me from your list", "email", ALL),
    ("take my number off your list", "call", ALL),
    ("leave me alone", "sms", ALL),
    ("do not contact me", "email", ALL),
    ("I want to opt out", "email", ALL),
    ("stop calling me, and stop emailing me too", "call", {CALL, EMAIL}),
])
def test_opt_out_scope_follows_the_leads_wording(said, channel, scope):
    r = oo.classify(said, channel)
    assert r.kind == oo.DND and r.confidence == oo.HIGH and set(r.scope) == scope, r


@pytest.mark.parametrize("said,channel", [
    ("What time is the open house?", "sms"), ("Yes, send me the details", "email"),
    ("please don't stop calling, I'm interested", "call"), ("I'll stop by the office tomorrow", "sms"),
    ("Thanks, that works", "email"), ("", "sms"),
])
def test_ordinary_replies_are_not_opt_outs(said, channel):
    assert oo.classify(said, channel).kind == oo.NONE


def test_not_interested_and_wrong_number_are_closes_not_dnd():
    assert oo.classify("no thanks", "sms").kind == oo.NOT_INTERESTED
    assert oo.classify("Sorry, wrong number", "sms").kind == oo.WRONG_NUMBER
    # but a stop request wins over "not interested"
    r = oo.classify("not interested, stop texting me", "sms")
    assert r.kind == oo.DND and set(r.scope) == {SMS}


def test_vague_hostile_or_legal_wording_is_unclear_not_auto_applied():
    for said in ("This is harassment, I will report you", "enough already", "I'm going to call my lawyer"):
        assert oo.classify(said, "email").kind == oo.UNCLEAR, said


def test_quoted_history_and_our_own_footer_are_not_opt_outs():
    reply = ("Thanks, sounds good!\n\nOn Tue, Sep 30, 2026 at 10:00 AM Cora <hi@colaberry.test> wrote:\n"
             "> Reply to unsubscribe. Stop receiving these emails here.\n> Text STOP to stop alerts")
    assert oo.classify(reply, "email").kind == oo.NONE
    bottom_post = "> Text STOP to stop alerts\nPlease remove me from this list"
    assert set(oo.classify(bottom_post, "email").scope) == set(ALL)
    assert oo.classify("Automatic reply: I am out of office. Click unsubscribe to leave this list", "email").kind == oo.NONE


def test_only_the_leads_own_call_lines_count():
    t = ("bot: Hi, this is Cora. If you want, I can remove you from our list or stop calling.\n"
         "human: Yeah I'm interested, tell me more\nbot: Great")
    assert oo.classify_call(t).kind == oo.NONE                   # the agent's words are not the lead's
    t2 = "bot: Hello\nhuman: Please stop calling me.\nbot: Understood, I'll remove you"
    r = oo.classify_call(t2)
    assert r.kind == oo.DND and set(r.scope) == {CALL}
    t3 = "human: take me off your list\nhuman: and don't call me"
    assert set(oo.classify_call(t3).scope) == set(ALL)
    assert oo.human_lines("bot: x\nhuman: y\nUser: z") == "y\nz"


# ───────────────────────── DB-backed (opt-in) ─────────────────────────

DB_URL = os.environ.get("WRONG_DATE_TEST_DATABASE_URL")
db = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")


class FakeGHL:
    def __init__(self, records=None):
        self.records = records or {}
        self.dnd_calls = []

    def get_contact(self, cid):
        return {"contact": self.records.get(cid, {"id": cid, "dnd": False, "dndSettings": {}, "tags": []})}

    def search_contact_by_phone(self, phone):
        return None

    def set_dnd(self, cid, channels, *, active=True, reason="", mode_flags=None):
        self.dnd_calls.append((cid, set(channels), active))
        return {}


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(DB_URL)
    with Session(engine) as s:
        for t in ("optout_actions", "inbound_messages", "call_events", "scheduled_jobs"):
            s.execute(text(f"DELETE FROM {t}"))
        s.execute(text("DELETE FROM lead_state"))
        s.execute(text("DELETE FROM app_config WHERE key LIKE 'optout_%'"))
        s.commit()
        with patch("app.core.mode_flags.get_mode_flags", return_value=NS(ghl_writes_enabled=True)), \
                patch("app.core.intent_actions._write_ghl_campaign_off"):
            yield s
        s.rollback()
    engine.dispose()


SETTINGS = NS(openai_model_consent_detector="gpt-4o-mini")


def _lead(session, cid, **cols):
    from tests.unit.test_wrong_date_safeguards import _lead_state

    _lead_state(session, cid, **cols)


def _msg(body, mid=None):
    return {"id": mid or str(uuid.uuid4()), "body": body}


def _rows(session):
    return session.execute(text("SELECT source, kind, scope, status, decided_by FROM optout_actions ORDER BY created_at")).fetchall()


@db
def test_sms_stop_sets_sms_dnd_only_and_is_idempotent(session):
    from app.services import optout

    ghl = FakeGHL()
    m = _msg("STOP", "m1")
    assert optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=m, contact_id="c1") == "applied"
    assert ghl.dnd_calls == [("c1", {"sms"}, True)]
    assert _rows(session) == [("sms_reply", "dnd", "sms", "applied", "auto")]
    assert optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=m, contact_id="c1") == "duplicate"
    assert len(ghl.dnd_calls) == 1


@db
def test_email_reply_wording_decides_the_channels(session):
    from app.services import optout

    ghl = FakeGHL()
    optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg("Please stop calling and texting me"), contact_id="c2")
    assert ghl.dnd_calls[-1][1] == {"call", "sms"}
    assert session.execute(text("SELECT do_not_call FROM lead_state WHERE contact_id='c2'")).fetchone() is None  # no lead row: skipped, no crash


@db
def test_call_scope_stops_cora_calling_but_sms_scope_does_not(session):
    from app.services import optout

    _lead(session, "calls-lead", status="active")
    _lead(session, "sms-lead", status="active")
    ghl = FakeGHL()
    optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("don't call me"), contact_id="calls-lead")
    optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("STOP"), contact_id="sms-lead")
    q = "SELECT do_not_call, status FROM lead_state WHERE contact_id=:c"
    assert tuple(session.execute(text(q), {"c": "calls-lead"}).fetchone()) == (True, "closed")
    assert tuple(session.execute(text(q), {"c": "sms-lead"}).fetchone()) == (False, "active")


@db
def test_unclear_reply_llm_decides_when_sure_else_human_reviews(session):
    from app.services import optout

    ghl = FakeGHL()
    body = "This is harassment"
    with patch.object(optout, "llm_judge", return_value=("opt_out_all", 0.95, "asks to be left alone")):
        assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg(body, "a"), contact_id="u1") == "applied"
    assert ghl.dnd_calls[-1][1] == set(ALL) and _rows(session)[-1][4] == "llm"
    with patch.object(optout, "llm_judge", return_value=("opt_out_all", 0.6, "maybe")):
        assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg(body, "b"), contact_id="u2") == "review"
    with patch.object(optout, "llm_judge", return_value=None):
        assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg(body, "c"), contact_id="u3") == "review"
    with patch.object(optout, "llm_judge", return_value=("other", 0.95, "just angry")):
        assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg(body, "d"), contact_id="u4") == "none"
    assert len(ghl.dnd_calls) == 1


@db
def test_llm_budget_and_switch_are_respected(session):
    from app.services import optout

    ghl = FakeGHL()
    budget = [0]
    with patch.object(optout, "llm_judge") as judge:
        assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg("this is harassment"),
                                   contact_id="b1", llm_budget=budget) == "review"
        assert not judge.called


@db
def test_review_apply_with_chosen_scope_dismiss_and_undo(session):
    from app.services import optout

    ghl = FakeGHL({"r1": {"id": "r1", "dnd": False, "dndSettings": {"Email": {"status": "active"}}}})
    with patch.object(optout, "llm_judge", return_value=None):
        optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("enough already", "x1"), contact_id="r1")
        optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("I have had enough of this", "x2"), contact_id="r2")
    ids = [r[0] for r in session.execute(text("SELECT id FROM optout_actions WHERE status='review' ORDER BY contact_id")).fetchall()]
    assert len(ids) == 2
    assert optout.review_apply(session, SETTINGS, ids[0], "kes", {"sms", "email"}, ghl=ghl) == "applied"
    assert ghl.dnd_calls[-1] == ("r1", {"sms"}, True)               # Email DND already on: only the missing channel is written
    optout.review_dismiss(session, ids[1], "kes")
    assert [r[3] for r in _rows(session)] == ["applied", "dismissed"]
    with pytest.raises(ValueError):
        optout.review_apply(session, SETTINGS, ids[1], "kes", ghl=ghl)           # already decided
    optout.undo(session, SETTINGS, ids[0], "kes", ghl=ghl)
    assert ghl.dnd_calls[-1] == ("r1", {"sms"}, False)             # Email DND pre-existed, so it is left alone
    assert _rows(session)[0][3] == "undone"


@db
def test_call_opt_out_by_the_leads_words_or_review_when_only_the_bot_said_it(session):
    from app.services import optout

    ghl = FakeGHL()
    with patch.object(optout, "_ghl", return_value=ghl):
        assert optout.handle_call(session, SETTINGS, contact_id="k1", call_event_id="ce1",
                                  transcript="bot: hi\nhuman: please stop calling me") == "applied"
        assert optout.handle_call(session, SETTINGS, contact_id="k2", call_event_id="ce2",
                                  transcript="bot: I can stop calling you if you like\nhuman: no it is fine") == "review"
        assert optout.handle_call(session, SETTINGS, contact_id="k1", call_event_id="ce1",
                                  transcript="human: stop calling me") == "duplicate"
    assert ghl.dnd_calls == [("k1", {"call"}, True)]


@db
def test_shadow_mode_records_but_writes_nothing(session):
    from app.services import optout

    ghl = FakeGHL()
    with patch("app.core.mode_flags.get_mode_flags", return_value=NS(ghl_writes_enabled=False)):
        assert optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("STOP"), contact_id="s1") == "shadow"
    assert ghl.dnd_calls == [] and _rows(session)[0][3] == "shadow"


@db
def test_reconcile_finds_do_not_call_leads_missing_dnd_in_ghl_and_is_resumable(session):
    from app.services import optout

    for c in ("have", "miss", "miss-all"):
        _lead(session, c, do_not_call=True)
    session.execute(text("""INSERT INTO call_events (id, call_id, contact_id, direction, detected_intent, transcript, dedupe_key, created_at)
        VALUES ('e1','e1','miss','outbound','do_not_call','bot: hi\nhuman: stop calling me', 'e1', now())"""))
    session.commit()
    ghl = FakeGHL({"have": {"id": "have", "dnd": True}})
    r = optout.reconcile_step(session, SETTINGS, ghl, limit=2)
    assert r["checked"] == 2
    r2 = optout.reconcile_step(session, SETTINGS, ghl, limit=10)
    assert r2["checked"] == 1
    assert optout.reconcile_step(session, SETTINGS, ghl, limit=10)["checked"] == 0           # nothing left
    got = {row[0]: (row[1], row[2]) for row in session.execute(text(
        "SELECT external_id, status, scope FROM optout_actions WHERE source='reconcile'")).fetchall()}
    assert got["have"][0] == "ok"
    assert got["miss"] == ("review", "call")                                  # from the lead's own words
    assert got["miss-all"] == ("review", "call,email,sms")                    # no wording on file -> all channels
    with patch.object(optout, "_ghl", return_value=ghl):
        out = optout.apply_batch(session, SETTINGS, "kes")
    assert out == {"applied": 2, "failed": 0, "remaining": 0}
    assert sorted((c[0], sorted(c[1])) for c in ghl.dnd_calls) == [
        ("miss", ["call"]), ("miss-all", ["call", "email", "sms"])]


@db
def test_cora_block_reason_covers_flags_stop_replies_and_pending_review(session):
    from app.services import optout

    _lead(session, "dnc", do_not_call=True)
    assert "do-not-contact" in optout.cora_block_reason(session, ["dnc"], "email")
    session.execute(text("INSERT INTO inbound_messages (id, contact_id, channel, body) VALUES (:i,'stopper','sms','STOP')"), {"i": str(uuid.uuid4())})
    session.commit()
    assert optout.cora_block_reason(session, ["stopper"], "sms")
    assert optout.cora_block_reason(session, ["stopper"], "email") is None            # STOP is an SMS opt-out
    ghl = FakeGHL()
    optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("stop texting me"), contact_id="textless")
    assert "sms" in optout.cora_block_reason(session, ["textless"], "sms")
    assert optout.cora_block_reason(session, ["textless"], "email") is None
    with patch.object(optout, "llm_judge", return_value=None):
        optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg("enough already"), contact_id="waiting")
    assert "awaiting review" in optout.cora_block_reason(session, ["waiting"], "email")
    assert optout.cora_block_reason(session, ["nobody"], "sms") is None


# ── regressions found live 2026-10-02 ──
@db
def test_permanent_dnd_is_never_overwritten_only_missing_channels_are_added(session):
    from app.services import optout

    ghl = FakeGHL({"p1": {"id": "p1", "dnd": False, "dndSettings": {"SMS": {"status": "permanent"}}}})
    assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg("please unsubscribe me"),
                               contact_id="p1") == "applied"
    assert ghl.dnd_calls == [("p1", {"call", "email"}, True)]          # SMS (permanent) untouched
    ghl2 = FakeGHL({"p2": {"id": "p2", "dnd": True}})
    assert optout.handle_reply(session, SETTINGS, ghl2, channel="email", message=_msg("remove me"), contact_id="p2") == "applied"
    assert ghl2.dnd_calls == []                                        # already DND everywhere: nothing written
    assert [r[3] for r in _rows(session)] == ["applied", "applied"]


class FailingGHL(FakeGHL):
    fail = True

    def set_dnd(self, *a, **k):
        if self.fail:
            raise RuntimeError("GHL HTTP error: 401 token is not authorized for this scope")
        return super().set_dnd(*a, **k)


@db
def test_a_failed_automatic_write_is_retried_and_capped(session):
    from app.services import optout

    ghl = FailingGHL()
    assert optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("STOP", "f1"), contact_id="f1") == "failed"
    assert _rows(session)[0][3] == "failed"
    assert optout.retry_failed(session, SETTINGS, ghl) == 0                    # still failing: attempt 2
    ghl.fail = False
    assert optout.retry_failed(session, SETTINGS, ghl) == 1
    assert _rows(session)[0][3] == "applied" and ghl.dnd_calls == [("f1", {"sms"}, True)]
    ghl.fail = True
    optout.handle_reply(session, SETTINGS, ghl, channel="sms", message=_msg("STOP", "f2"), contact_id="f2")
    for _ in range(8):
        optout.retry_failed(session, SETTINGS, ghl)
    attempts = session.execute(text("SELECT previous_state->>'attempts' FROM optout_actions WHERE external_id='f2'")).scalar()
    assert attempts == "5"                                                     # gives up after 5


@db
def test_operator_apply_that_fails_stays_visible_on_the_tile_and_out_of_the_batch(session):
    from app.services import optout

    _lead(session, "o1", do_not_call=True)
    ghl = FakeGHL()
    optout.reconcile_step(session, SETTINGS, ghl, limit=5)
    [rid] = [r[0] for r in session.execute(text("SELECT id FROM optout_actions WHERE status='review'")).fetchall()]
    bad = FailingGHL()
    assert optout.review_apply(session, SETTINGS, rid, "kes", ghl=bad) == "review"
    row = session.execute(text("SELECT status, reason FROM optout_actions WHERE id=:i"), {"i": rid}).fetchone()
    assert row[0] == "review" and row[1].startswith("apply failed:")
    with patch.object(optout, "_ghl", return_value=bad):
        out = optout.apply_batch(session, SETTINGS, "kes")
    assert out["remaining"] == 0                                               # failed item is not re-tried forever
    bad.fail = False
    assert optout.review_apply(session, SETTINGS, rid, "kes", ghl=bad) == "applied"


# ── false positives found scanning production transcripts (2026-10-01) ──
@pytest.mark.parametrize("said,channel", [
    ("I never received the email", "email"), ("please don't forget to email me the details", "email"),
    ("take me through the program", "call"), ("I don't have time to call you back", "call"),
    ("Please do not leave a message after the tone", "call"), ("I have enough information, thanks", "email"),
    ("I don't want to miss the open house, can you text me the link?", "sms"),
    ("Never mind, I found it", "sms"),
])
def test_ordinary_sentences_that_share_words_with_opt_outs_are_not_opt_outs(said, channel):
    assert oo.classify(said, channel).kind in (oo.NONE, oo.UNCLEAR), said


def test_dont_text_call_me_opts_out_of_sms_only():
    r = oo.classify("can you call me tomorrow? don't text, call", "sms")
    assert r.kind == oo.DND and set(r.scope) == {SMS}


@pytest.mark.parametrize("said,channel,scope", [
    ("don't send me any more emails", "email", {EMAIL}), ("I asked you not to call me", "call", {CALL}),
    ("never call me again", "call", {CALL}), ("stop sending me texts", "sms", {SMS}),
    ("don't call or text me", "call", {CALL, SMS}), ("take me off your list", "call", ALL),
])
def test_adjacent_verb_and_object_still_match(said, channel, scope):
    r = oo.classify(said, channel)
    assert r.kind == oo.DND and set(r.scope) == scope, r


# ── found live: Cora's own email logged back as an "inbound reply" is not an opt-out ──
OWN_EMAIL = ("Hi there! This is Cora from Colaberry. I called earlier to connect but missed you. If you have questions, "
             "feel free to reply! Text STOP to stop alerts. If you no longer wish to receive these emails you may "
             "unsubscribe [https://services.msgsndr.com/emails/build")


def test_cora_own_email_logged_back_as_inbound_is_never_an_opt_out():
    assert oo.is_own_echo(OWN_EMAIL)
    assert oo.classify(OWN_EMAIL, "email").kind == oo.NONE
    assert oo.is_own_echo("Discover Your Potential with Colaberry!\n\n\nIf you no longer wish to receive these emails you may unsubscribe")
    assert oo.classify("Hi there,\n\n" + OWN_EMAIL, "email").kind == oo.NONE


def test_a_real_reply_that_quotes_cora_is_still_judged_on_the_leads_own_words():
    quoted_unmarked = "Please remove me from your list.\n\n" + OWN_EMAIL
    assert not oo.is_own_echo(quoted_unmarked)
    r = oo.classify(quoted_unmarked, "email")
    assert r.kind == oo.DND and set(r.scope) == set(ALL)
    marked = "Thanks, sounds good!\n\nOn Tue, Sep 30, 2026 at 10:00 AM Cora <hi@c.test> wrote:\n> " + OWN_EMAIL
    assert oo.classify(marked, "email").kind == oo.NONE


@db
def test_echo_is_not_recorded_as_a_reply_and_never_reaches_dnd(session):
    from app.services import optout

    ghl = FakeGHL()
    assert optout.handle_reply(session, SETTINGS, ghl, channel="email", message=_msg(OWN_EMAIL, "echo1"), contact_id="e1") == "none"
    assert ghl.dnd_calls == [] and _rows(session) == []


# ── boilerplate from other senders / templates that tells the reader how to stop is never an opt-out ──
STRIPE = "Stripe: Reply STOP to cancel. Msg&Data rates may apply. Msg frequency varies. Contact support at https://support.stripe.com"
REMINDER = ("Hi Sam, Just a friendly reminder that your appointment is in one hour. Please use this Zoom Meeting Link: "
            "https://zoom.us/j/123 " + "See you soon. " * 30 + "To unsubscribe from these reminders click here.")
OTHER_COLABERRY = "Hi there! Cora from Colaberry here. Our class starts soon and we're hosting a FREE Open House. Unsubscribe"


def test_third_party_stop_instructions_and_footers_are_not_opt_outs():
    assert oo.classify(STRIPE, "sms").kind == oo.NONE and oo.is_own_echo(STRIPE)
    assert oo.classify(REMINDER, "email").kind == oo.NONE
    assert oo.classify(OTHER_COLABERRY, "email").kind == oo.NONE and oo.is_own_echo(OTHER_COLABERRY)


def test_real_short_replies_still_work_after_the_boilerplate_rules():
    assert oo.classify("STOP", "sms").kind == oo.DND
    assert oo.classify("Stop calling", "sms").kind == oo.DND
    r = oo.classify("Please unsubscribe me. Thanks\n\n" + "Regards, Sam. " * 50, "email")
    assert r.kind == oo.DND and set(r.scope) == set(ALL)                       # request is in the first lines
    late = "Thanks for the info, very helpful.\n" + ("Best regards. " * 40) + "unsubscribe"
    assert oo.classify(late, "email").kind == oo.NONE                          # "unsubscribe" far down = footer


def test_verification_code_texts_with_stop_links_are_not_opt_outs():
    t = "Your Link verification code is: 150571. To stop receiving these messages, visit support.link.com/sms-opt-out"
    assert oo.classify(t, "sms").kind == oo.NONE


# ── "unclear" is only a nomination: weak word hits no longer reach a human (found live 2026-10-01) ──
@pytest.mark.parametrize("said,channel", [
    ("Hey. You just missed me. So just please text me or leave me a message over here.", "call"),
    ("I am waiting to receive the email, its not being sent to my email box. Its not in my spam cos I have checked it several times.", "sms"),
])
def test_lead_asking_to_be_contacted_is_not_even_nominated(said, channel):
    assert oo.classify(said, channel).kind == oo.NONE


@pytest.mark.parametrize("said,kind", [("this is spam, I will report you", oo.UNCLEAR), ("leave me alone", oo.DND),
                                       ("you are spamming me", oo.UNCLEAR)])
def test_real_spam_complaints_are_still_caught(said, kind):
    assert oo.classify(said, "sms").kind == kind


@db
def test_contact_details_are_looked_up_once_and_stored(session):
    from app.services import optout

    _lead(session, "x", do_not_call=True)
    optout._propose(session, contact_id="+14155550123", scope={"call"}, source="history_call", external_id="h1", phrase="p", excerpt="e", reason="r")
    optout._propose(session, contact_id="ghl-abc", scope={"call"}, source="history_call", external_id="h2", phrase="p", excerpt="e", reason="r")
    session.commit()
    calls = []

    class G:
        def get_contact(self, cid):
            calls.append(cid)
            return {"contact": {"phone": "+16145550100", "email": "lead@example.test"}}

    snap = optout.snapshot(session, G())
    by = {r["contact_id"]: r for r in snap["review"]}
    assert by["+14155550123"]["contact_phone"] == "+14155550123" and by["+14155550123"]["contact_email"] == ""
    assert (by["ghl-abc"]["contact_phone"], by["ghl-abc"]["contact_email"]) == ("+16145550100", "lead@example.test")
    assert calls == ["ghl-abc"]                                           # a phone-number id needs no lookup
    session.commit()
    optout.snapshot(session, G())
    assert calls == ["ghl-abc"]                                           # stored: not looked up again


@db
def test_llm_triage_dismisses_clear_non_requests_and_annotates_the_rest(session):
    from app.services import optout

    for i, ex in enumerate(["leave me a message", "stop it already", "unreadable"]):
        optout._insert(session, contact_id=f"t{i}", source="history_call", external_id=f"t{i}", kind="dnd", scope={"call"},
                       confidence="medium", decided_by="auto", status="review", phrase="p", excerpt=ex,
                       reason="Historical call: unclear wording - please read the quote")
    session.commit()
    answers = {"leave me a message": ("other", 0.95, "asks for a voicemail"), "stop it already": ("opt_out_call", 0.8, "asks to stop"),
               "unreadable": None}
    with patch.object(optout, "llm_judge", side_effect=lambda s, text_, ch: answers[text_]):
        out = optout.triage_unclear(session, SETTINGS)
    assert out == {"dismissed": 1, "annotated": 1, "skipped": 1}
    rows = {r[0]: r[1:] for r in session.execute(text("select excerpt, status, scope, reason, decided_by from optout_actions order by created_at")).fetchall()}
    assert rows["leave me a message"][0] == "dismissed" and rows["leave me a message"][3] == "llm"
    assert rows["stop it already"][0] == "review" and "[LLM 0.80]" in rows["stop it already"][2]
    assert rows["unreadable"][0] == "review"


class NoSuchContactGHL(FakeGHL):
    def search_contact_by_phone(self, phone):
        return None                                     # GHL has never heard of this number


@db
def test_apply_on_a_number_with_no_ghl_contact_succeeds_as_a_recorded_no_op_and_can_be_undone(session):
    from app.services import optout

    optout._propose(session, contact_id="33569149532", scope={"call", "sms", "email"}, source="history_call",
                    external_id="nc1", phrase="stop", excerpt="Wanna stop that?", reason="unclear")
    session.commit()
    [rid] = [r[0] for r in session.execute(text("select id from optout_actions where external_id='nc1'")).fetchall()]
    ghl = NoSuchContactGHL()
    assert optout.review_apply(session, SETTINGS, rid, "kes", ghl=ghl) == "applied"
    row = session.execute(text("select status, reason from optout_actions where id=:i"), {"i": rid}).fetchone()
    assert row[0] == "applied" and "no GHL contact exists" in row[1]
    assert ghl.dnd_calls == []                                           # nothing written to GHL
    optout.undo(session, SETTINGS, rid, "kes", ghl=ghl)                  # undo also needs no GHL contact
    assert session.execute(text("select status from optout_actions where id=:i"), {"i": rid}).scalar() == "undone"
    assert ghl.dnd_calls == []


@db
def test_a_number_with_a_lead_record_but_no_ghl_contact_still_stops_cora_calling_it(session):
    from app.services import optout

    _lead(session, "+13145550100", status="active")
    ghl = NoSuchContactGHL()
    st = optout.apply_dnd(session, SETTINGS, ghl, contact_id="+13145550100", scope={"call"}, source="history_call",
                          external_id="nc2", phrase="stop calling", excerpt="Stop calling", decided_by="kes")
    assert st == "applied" and ghl.dnd_calls == []
    assert tuple(session.execute(text("select do_not_call, status from lead_state where contact_id='+13145550100'")).fetchone()) == (True, "closed")
