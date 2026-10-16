"""Content audit exemptions: the LLM decline-vs-offer judge, company-name strip and footer strip (spec/35). No DB, no real LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core import content_audit as ca
from app.services import content_judge as cj
from app.services import delivery_sync as ds

NOW = datetime(2026, 10, 12, 16, 10, tzinfo=timezone.utc)
OLU_REPLY = ("Colaberry is no longer enrolling new students in the Data Analytics bootcamp. Instead, we offer the "
             "AI Systems Architect Accelerator - a hands-on, 12-week online program.")


@pytest.fixture(autouse=True)
def _clear_cache():
    cj._cache.clear()
    yield
    cj._cache.clear()


# ── pure logic ───────────────────────────────────────────────────────────────

def test_course_only_separates_course_names_from_employment_claims():
    assert ca.course_only(["data analytics", "bootcamp"]) is True
    assert ca.course_only(["bootcamp", "placement rate"]) is False
    assert ca.course_only([]) is False


def test_prior_inbound_is_the_latest_inbound_before_the_message():
    def t(mins):
        return (NOW + timedelta(minutes=mins)).isoformat()

    msgs = [{"dateAdded": t(-5), "direction": "inbound", "body": "old"},
            {"dateAdded": t(-1), "direction": "inbound", "body": "I am interested in the data analytics class"},
            {"dateAdded": t(0), "direction": "outbound", "body": OLU_REPLY},
            {"dateAdded": t(1), "direction": "inbound", "body": "No I'm not interested"}]
    def parse(s):
        return datetime.fromisoformat(s) if s else None

    assert ca.prior_inbound(msgs, msgs[2], parse) == "I am interested in the data analytics class"
    assert ca.prior_inbound(msgs[:1], msgs[0], parse) is None


# ── judge (OpenAI mocked) ────────────────────────────────────────────────────

DECLINE_BODY = "We are no longer enrolling in the Data Analytics bootcamp."


def _client(out=None, exc=None):
    c = MagicMock()
    if exc:
        c.chat_completion.side_effect = exc
    else:
        c.chat_completion.return_value = out
    return c


@pytest.mark.parametrize("out,want", [
    ({"verdict": "declines", "reason": "x"}, cj.DECLINES),
    ({"verdict": "offers"}, cj.OFFERS),
    ({"verdict": "UNCLEAR"}, cj.UNCLEAR),
    ({"verdict": "maybe"}, cj.UNCLEAR),            # off-schema -> stays flagged
    ({}, cj.UNCLEAR),
])
def test_judge_maps_verdicts_and_defaults_to_unclear(out, want):
    with patch("app.adapters.openai_client.OpenAIClient", return_value=_client(out)):
        assert cj.judge(SimpleNamespace(), "m1", DECLINE_BODY, None) == want


def test_judge_failure_is_unclear_and_not_cached():
    with patch("app.adapters.openai_client.OpenAIClient", return_value=_client(exc=RuntimeError("boom"))):
        assert cj.judge(SimpleNamespace(), "m1", "text", None) == cj.UNCLEAR
    assert "m1" not in cj._cache


def test_judge_caches_a_verdict_per_message():
    c = _client({"verdict": "declines"})
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        cj.judge(SimpleNamespace(), "m1", "text", None)
        cj.judge(SimpleNamespace(), "m1", "text", None)
    assert c.chat_completion.call_count == cj.JUDGE_VOTES      # asked JUDGE_VOTES times once, then cached


# ── _flag_content wiring ─────────────────────────────────────────────────────

def _run(body, verdict=None, budget=None, config=None, conv=None, mid="m1", channel="sms", contact_email=None, ok_events=None):
    """Run _flag_content with the judge, the event writer and the GHL contact lookup stubbed.
    Returns (flag_events_written, judge_calls). Messages the judge clears are recorded as kind 'content_ok'
    rows, collected in `ok_events` (not in the flag events)."""
    events, calls = [], []
    ok_events = [] if ok_events is None else ok_events

    def fake_judge(settings, message_id, text, prior, terms=None):
        calls.append((text, prior))
        return verdict or cj.UNCLEAR

    def write(*a, **kw):
        (ok_events if kw.get("kind") == "content_ok" else events).append(kw)

    cfg = {"offer_forbidden_terms": None, **(config or {})}
    with patch.object(ds, "_upsert_event", write), \
            patch.object(ds, "_contact_email", lambda settings, cid: contact_email), \
            patch("app.services.content_judge.judge", fake_judge), \
            patch("app.core.app_config.get_config_value", lambda key, s, st, default=None: cfg.get(key, default)):
        ds._flag_content(None, SimpleNamespace(), {"id": mid, "body": body, "source": "app"}, channel, "c1", NOW, NOW,
                         conv_msgs=conv, llm_budget=budget)
    return events, calls


def test_olus_decline_is_not_flagged_when_the_judge_says_declines():
    events, calls = _run(OLU_REPLY, verdict=cj.DECLINES)
    assert events == [] and len(calls) == 1


@pytest.mark.parametrize("verdict", [cj.OFFERS, cj.UNCLEAR])
def test_offers_and_unclear_stay_flagged_with_the_verdict_recorded(verdict):
    events, _ = _run("Our Data Analytics bootcamp starts Monday", verdict=verdict)
    assert len(events) == 1 and events[0]["detail"]["llm_verdict"] == verdict


def test_employment_claims_never_reach_the_judge_even_with_a_decline_phrase():
    events, calls = _run("We no longer run the bootcamp but our placement rate is 71%", verdict=cj.DECLINES)
    assert len(events) == 1 and calls == []


def test_exhausted_budget_or_disabled_llm_keeps_the_flag():
    assert len(_run(OLU_REPLY, verdict=cj.DECLINES, budget=[0])[0]) == 1
    events, calls = _run(OLU_REPLY, verdict=cj.DECLINES, config={"content_llm_enabled": "false"})
    assert len(events) == 1 and calls == []


def test_budget_is_spent_per_judgement():
    budget = [2]
    _run(OLU_REPLY, verdict=cj.DECLINES, budget=budget)
    assert budget == [1]


def test_clean_message_makes_no_llm_call_and_no_flag():
    events, calls = _run("Hi, thanks for your interest in the AI Systems Architect Accelerator", verdict=cj.DECLINES)
    assert events == [] and calls == []


def test_interview_text_with_a_job_title_is_flagged_even_with_the_old_marker():
    """The [Interview Notification] exemption was removed 2026-10-13 (Ali condition 1): a prefix is a password anyone can type."""
    for body in ("[Interview Notification] Your interview for the Data Analyst position is Friday 2 PM CT.",
                 "Your interview for the Data Analyst position is Friday 2 PM CT."):
        events, _ = _run(body, verdict=cj.UNCLEAR)
        assert len(events) == 1


def test_marketing_text_copying_the_old_marker_is_flagged():
    events, _ = _run("[Interview Notification] Our Data Analytics bootcamp starts Monday", verdict=cj.UNCLEAR)
    assert len(events) == 1


def test_interview_notice_without_any_listed_term_is_not_flagged():
    """What InboxGenius sends today: no job title, so no exemption is needed."""
    events, calls = _run("Hi Sam, your interview with Acme for the position you applied for is on Thu Oct 8 at 2:00 PM CT.")
    assert events == [] and calls == []


def test_employment_claim_is_flagged_and_never_sent_to_the_judge():
    events, calls = _run("[Interview Notification] Data Analyst role, 90% placement rate", verdict=cj.DECLINES)
    assert len(events) == 1 and "placement rate" in events[0]["detail"]["terms"] and calls == []


# ── company name in a signature (Claudia Zapata email, 2026-10-12) ───────────

SIGNATURE_EMAIL = ("Hello Claudia, Please reach out to Dhee at dhee@colaberry.com regarding all internship inquiries. "
                   "Thank you. TAIWO OLUDIMIMU K Colaberry School of Data Analytics 972-992-1024 support@colaberry.com "
                   "training.colaberry.com facebook.com/colaberryschoolofdataanalytics")


def test_company_name_in_a_signature_is_not_flagged_and_needs_no_llm():
    events, calls = _run(SIGNATURE_EMAIL, verdict=cj.OFFERS)
    assert events == [] and calls == []


def test_only_the_brand_phrase_is_removed_a_course_named_beside_it_is_still_flagged():
    events, _ = _run("Join our Data Analytics bootcamp. Colaberry School of Data Analytics", verdict=cj.OFFERS)
    assert len(events) == 1 and "bootcamp" in events[0]["detail"]["terms"]


def test_brand_phrases_are_configurable_and_empty_disables():
    assert len(_run(SIGNATURE_EMAIL, verdict=cj.OFFERS, config={"brand_phrases": ""})[0]) == 1


def test_strip_brand_is_case_insensitive_and_longest_first():
    out = ca.strip_brand("A COLABERRY SCHOOL OF DATA ANALYTICS b", ca.parse_brand_phrases(None))
    assert "analytics" not in out.lower() and "school" not in out.lower()


# ── judge votes + footer stripping (Ali 2026-10-13, replay of the 7 stored flags) ─────────────────────────────

def _client_seq(*outs):
    c = MagicMock()
    c.chat_completion.side_effect = list(outs)
    return c


@pytest.mark.parametrize("votes,want", [
    (("declines", "declines"), cj.DECLINES),
    (("declines", "unclear"), cj.UNCLEAR),         # a split vote must NOT clear the flag
    (("unclear", "declines"), cj.UNCLEAR),
    (("declines", "offers"), cj.OFFERS),           # any 'offers' wins
    (("offers", "declines"), cj.OFFERS),
    (("unclear", "unclear"), cj.UNCLEAR),
    (("declines", "maybe"), cj.UNCLEAR),           # off-schema vote counts as unclear
])
def test_judge_clears_only_when_every_vote_declines(votes, want):
    c = _client_seq(*({"verdict": v} for v in votes))
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        assert cj.judge(SimpleNamespace(), "mv", DECLINE_BODY, None) == want
    assert c.chat_completion.call_count == cj.JUDGE_VOTES


def test_one_failed_ask_is_unclear_and_not_cached():
    c = _client_seq({"verdict": "declines"}, RuntimeError("boom"))
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        assert cj.judge(SimpleNamespace(), "mf", "text", None) == cj.UNCLEAR
    assert "mf" not in cj._cache


MOCK_INTERVIEW_EMAIL = (
    "Mock interview notification. Hi taiwo, This is a TEST of the Colaberry interview notification email. Your Data Analyst "
    "interview with Test Company is Thursday, October 8 at 2:00 PM CT, by video. No action is needed for this test. "
    "If you no longer wish to receive these emails you may unsubscribe [https://services.msgsndr.com/emails/builder/unsubscribe/x] "
    "View this email in browser")


def test_strip_footer_removes_unsubscribe_boilerplate_but_keeps_the_message():
    out = ca.strip_footer(MOCK_INTERVIEW_EMAIL)
    assert "no longer wish" not in out.lower() and "unsubscribe" not in out.lower() and "browser" not in out.lower()
    assert "Data Analyst interview with Test Company" in out


def test_strip_footer_keeps_a_genuine_decline_sentence():
    text = "We no longer enroll new students in the Data Analytics bootcamp. Instead we offer the AI Accelerator."
    assert ca.strip_footer(text) == text


def test_strip_footer_handles_empty():
    assert ca.strip_footer(None) == "" and ca.strip_footer("") == ""


def test_judge_sees_the_body_without_the_footer_but_the_keyword_scan_still_finds_a_footer_term():
    events, calls = _run(MOCK_INTERVIEW_EMAIL, verdict=cj.UNCLEAR)
    assert len(events) == 1 and len(calls) == 1
    assert "no longer wish" not in calls[0][0].lower()           # judge input is footer-free
    events, _ = _run("Hello. Unsubscribe here: Data Analytics bootcamp", verdict=cj.UNCLEAR)
    assert len(events) == 1                                       # a term inside a footer-like sentence is still flagged


def test_judge_sends_the_flagged_terms_and_the_whole_long_message_to_the_model():
    c = _client({"verdict": "offers"})
    long_body = ("Intro. " * 200) + "Join our Data Analytics bootcamp, seats are open."        # the offer sits past 800 chars
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        cj.judge(SimpleNamespace(), "mlong", long_body, "hello", terms=["data analytics", "bootcamp"])
    user = c.chat_completion.call_args_list[0].args[0][1]["content"]
    assert "Flagged terms: data analytics, bootcamp" in user and "seats are open" in user and "hello" in user


def test_the_previous_prompts_word_declines_is_still_understood_as_not_offering():
    c = _client({"verdict": "declines"})
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        assert cj.judge(SimpleNamespace(), "mold", "x", None) == cj.NOT_OFFERING


def test_there_is_no_wording_list_in_the_judge_module():
    """Kes 2026-10-14: no whack-a-mole phrase lists - the model decides from the whole message."""
    assert not hasattr(cj, "has_decline_phrase") and not hasattr(cj, "_DECLINE_PHRASE")


def test_context_decides_not_keywords_so_explaining_the_change_clears_when_the_judge_says_not_offering():
    explain = "Hi Olu, Colaberry has now transitioned from offering a standalone Data Analytics program to focusing on AI training."
    ok = []
    events, calls = _run(explain, verdict=cj.NOT_OFFERING, ok_events=ok)
    assert events == [] and len(calls) == 1
    assert len(ok) == 1 and ok[0]["detail"]["verdict"] == cj.NOT_OFFERING and ok[0]["detail"]["terms"] == ["data analytics"]


def test_an_offer_stays_flagged_and_leaves_no_content_ok_row():
    ok = []
    events, _ = _run("Join our Data Analytics bootcamp, seats are open.", verdict=cj.OFFERS, ok_events=ok)
    assert len(events) == 1 and ok == []


@pytest.mark.parametrize("verdict", [cj.UNCLEAR, cj.OFFERS])
def test_doubt_or_offer_never_clears(verdict):
    ok = []
    events, _ = _run("Rather than focusing on Data Analytics alone, the new program teaches AI.", verdict=verdict, ok_events=ok)
    assert len(events) == 1 and ok == []


def test_a_decline_that_also_says_start_free_stays_flagged_and_never_reaches_the_judge():
    """TWC wording rule (Ali 2026-10-04): 'free' is a claim, not a course name - the decline judge cannot clear it."""
    events, calls = _run("We no longer enroll in the Data Analytics bootcamp, but you can start free at myfreeaiclass.com.",
                         verdict=cj.DECLINES)
    assert calls == [] and len(events) == 1 and "free" in events[0]["detail"]["terms"]


def test_feel_free_in_a_staff_text_is_not_flagged():
    events, calls = _run("Hi Sam, feel free to call me back anytime.")
    assert events == [] and calls == []


# ── emails delivered to our own staff are not audited (AI Call Recap false positive, 2026-10-14) ─────────────────

RECAP = ("Your AI Employee has handled another call for Colaberry! Call Summary: The user, an alumnus of Colaberry, called "
         "to ask how to present his experience on his resume for a senior Power BI developer position.")


@pytest.mark.parametrize("address,want", [
    ("staff@colaberry.com", True),
    ("Staff@Colaberry.COM", True),
    ("narendra@trillionsquare.com", True),
    ("a@mail.colaberry.com", True),                 # subdomain of an internal domain
    ("lead@gmail.com", False),
    ("x@colaberry.com.evil.com", False),            # look-alike
    ("x@notcolaberry.com", False),
    ("colaberry.com", False),                       # not an address
    ("a@b@colaberry.com", False),
    ("", False), (None, False),
])
def test_is_internal_email(address, want):
    assert ca.is_internal_email(address, ca.parse_internal_domains(None)) is want


def test_internal_domain_config_parsing():
    assert ca.parse_internal_domains(None) == ("colaberry.com", "trillionsquare.com")
    assert ca.parse_internal_domains(" @Foo.com , bar.org ") == ("foo.com", "bar.org")
    assert ca.parse_internal_domains("") == ()
    assert ca.is_internal_email("a@colaberry.com", ca.parse_internal_domains("")) is False


def test_recap_email_to_a_staff_contact_is_recorded_as_internal_not_flagged_and_skips_the_judge():
    events, calls = _run(RECAP, channel="email", contact_email="aleem@colaberry.com")
    assert [e["kind"] for e in events] == ["content_internal"] and "power bi" in events[0]["detail"]["terms"]
    assert calls == []


def test_an_employment_claim_to_a_staff_contact_is_still_flagged():
    events, _ = _run("Our job placement rate is 90 percent.", channel="email", contact_email="aleem@colaberry.com")
    assert [e["kind"] for e in events] == ["content_flag"]


def test_the_same_text_to_an_outside_lead_is_still_flagged():
    events, _ = _run(RECAP, channel="email", contact_email="someone@gmail.com")
    assert len(events) == 1 and "power bi" in events[0]["detail"]["terms"]


@pytest.mark.parametrize("email", [None, "", "not-an-address"])
def test_unreadable_or_missing_contact_email_keeps_the_message_audited(email):
    events, _ = _run(RECAP, channel="email", contact_email=email)
    assert len(events) == 1


def test_texts_are_always_audited_even_if_the_contact_has_a_staff_email():
    events, _ = _run(RECAP, channel="sms", contact_email="aleem@colaberry.com")
    assert len(events) == 1


def test_empty_config_disables_the_staff_exemption():
    events, _ = _run(RECAP, channel="email", contact_email="aleem@colaberry.com", config={"audit_internal_email_domains": ""})
    assert len(events) == 1


def test_config_can_replace_the_internal_domain_list():
    events, _ = _run(RECAP, channel="email", contact_email="x@partner.org", config={"audit_internal_email_domains": "partner.org"})
    assert [e["kind"] for e in events] == ["content_internal"]
    events, _ = _run(RECAP, channel="email", contact_email="x@colaberry.com", config={"audit_internal_email_domains": "partner.org"})
    assert [e["kind"] for e in events] == ["content_flag"]


def test_clean_email_to_staff_does_not_even_look_up_the_contact(monkeypatch):
    looked_up, events = [], []
    monkeypatch.setattr(ds, "_contact_email", lambda settings, cid: looked_up.append(cid) or "a@colaberry.com")
    monkeypatch.setattr(ds, "_upsert_event", lambda *a, **kw: events.append(kw))
    with patch("app.core.app_config.get_config_value", lambda key, s, st, default=None: default):
        ds._flag_content(None, SimpleNamespace(), {"id": "m9", "body": "Hi Sam, your interview is on Thursday.", "source": "app"},
                         "email", "c1", NOW, NOW)
    assert events == [] and looked_up == []             # no hit -> no GHL read


def test_contact_email_helper_reads_ghl_once_and_caches(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, settings=None, **kw): pass
        def get_contact(self, cid):
            calls.append(cid)
            return {"contact": {"email": "Aleem@Colaberry.com "}}

    ds._CONTACT_EMAIL_CACHE.clear()
    monkeypatch.setattr("app.adapters.ghl.GHLClient", FakeClient)
    assert ds._contact_email(SimpleNamespace(), "cidX") == "aleem@colaberry.com"
    assert ds._contact_email(SimpleNamespace(), "cidX") == "aleem@colaberry.com"
    assert calls == ["cidX"]
    ds._CONTACT_EMAIL_CACHE.clear()


def test_contact_email_helper_failure_returns_none_and_is_not_cached(monkeypatch):
    class Boom:
        def __init__(self, settings=None, **kw): pass
        def get_contact(self, cid): raise RuntimeError("ghl down")

    ds._CONTACT_EMAIL_CACHE.clear()
    monkeypatch.setattr("app.adapters.ghl.GHLClient", Boom)
    assert ds._contact_email(SimpleNamespace(), "cidY") is None
    assert "cidY" not in ds._CONTACT_EMAIL_CACHE
