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

def _run(body, verdict=None, budget=None, config=None, conv=None, mid="m1"):
    """Run _flag_content with the judge and event writer stubbed. Returns (events_written, judge_calls)."""
    events, calls = [], []

    def fake_judge(settings, message_id, text, prior):
        calls.append((text, prior))
        return verdict or cj.UNCLEAR

    cfg = {"offer_forbidden_terms": None, **(config or {})}
    with patch.object(ds, "_upsert_event", lambda *a, **kw: events.append(kw)), \
            patch("app.services.content_judge.judge", fake_judge), \
            patch("app.core.app_config.get_config_value", lambda key, s, st, default=None: cfg.get(key, default)):
        ds._flag_content(None, SimpleNamespace(), {"id": mid, "body": body, "source": "app"}, "sms", "c1", NOW, NOW,
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


@pytest.mark.parametrize("text,want", [
    ("We are no longer enrolling in the Data Analytics bootcamp.", True),
    ("That course is not available anymore.", True),
    ("The Excel program was discontinued last year.", True),
    ("We have retired the bootcamp.", True),
    ("The course isn't offered any more.", True),
    ("Your Data Analyst interview with Test Company is Thursday at 2 PM.", False),
    ("Our Data Analytics bootcamp starts Monday, want a spot?", False),
    ("Yes, the Tableau course is still available.", False),
    ("", False), (None, False),
])
def test_has_decline_phrase(text, want):
    assert cj.has_decline_phrase(text) is want


def test_model_declines_but_text_has_no_decline_phrase_stays_flagged():
    """Replay finding 2026-10-13: the model said 'declines' for an interview notice that merely names a job title."""
    c = _client({"verdict": "declines"})
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        v = cj.judge(SimpleNamespace(), "mi", "Your Data Analyst interview with Test Company is Thursday at 2 PM CT.", None)
    assert v == cj.UNCLEAR


def test_offer_that_contains_the_word_still_is_not_cleared_even_if_model_says_declines():
    c = _client({"verdict": "declines"})
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        assert cj.judge(SimpleNamespace(), "mo", "Our Data Analytics bootcamp starts Monday, want a spot?", None) == cj.UNCLEAR


def test_a_decline_that_also_says_start_free_stays_flagged_and_never_reaches_the_judge():
    """TWC wording rule (Ali 2026-10-04): 'free' is a claim, not a course name - the decline judge cannot clear it."""
    events, calls = _run("We no longer enroll in the Data Analytics bootcamp, but you can start free at myfreeaiclass.com.",
                         verdict=cj.DECLINES)
    assert calls == [] and len(events) == 1 and "free" in events[0]["detail"]["terms"]


def test_feel_free_in_a_staff_text_is_not_flagged():
    events, calls = _run("Hi Sam, feel free to call me back anytime.")
    assert events == [] and calls == []
