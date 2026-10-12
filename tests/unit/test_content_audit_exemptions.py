"""Content audit exemptions: interview-notification marker and the LLM decline-vs-offer judge (spec/35). No DB, no real LLM."""
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

@pytest.mark.parametrize("body,want", [
    ("[Interview Notification] Your interview for the Data Analyst position is on Friday.", True),
    ("  [interview   notification] hi", True),
    ("<html><body><p>[Interview Notification]</p><p>Hello</p></body></html>", True),
    ("Hi! [Interview Notification] Data Analyst bootcamp starts Monday", False),    # marker not at the start
    ("[Interview Notice] Data Analyst", False),                                      # typo does not count
    ("", False),
])
def test_interview_marker_must_start_the_visible_text(body, want):
    assert ca.is_interview_notice(body) is want


def test_empty_marker_config_disables_the_exemption():
    assert ca.is_interview_notice("[Interview Notification] hi", "") is False


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
        assert cj.judge(SimpleNamespace(), "m1", "text", None) == want


def test_judge_failure_is_unclear_and_not_cached():
    with patch("app.adapters.openai_client.OpenAIClient", return_value=_client(exc=RuntimeError("boom"))):
        assert cj.judge(SimpleNamespace(), "m1", "text", None) == cj.UNCLEAR
    assert "m1" not in cj._cache


def test_judge_caches_a_verdict_per_message():
    c = _client({"verdict": "declines"})
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        cj.judge(SimpleNamespace(), "m1", "text", None)
        cj.judge(SimpleNamespace(), "m1", "text", None)
    assert c.chat_completion.call_count == 1


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


def test_interview_notification_with_a_job_title_is_not_flagged_and_skips_the_judge():
    events, calls = _run("[Interview Notification] Your interview for the Data Analyst position is Friday 2 PM CT.")
    assert events == [] and calls == []


def test_same_text_without_the_marker_is_flagged():
    events, _ = _run("Your interview for the Data Analyst position is Friday 2 PM CT.", verdict=cj.UNCLEAR)
    assert len(events) == 1


def test_interview_marker_does_not_exempt_an_employment_claim():
    events, _ = _run("[Interview Notification] Data Analyst role, 90% placement rate")
    assert len(events) == 1 and events[0]["detail"]["terms"] == ["placement rate"]


def test_interview_marker_is_configurable():
    events, _ = _run("[Hiring Update] Data Analyst interview Friday", config={"interview_marker": "[Hiring Update]"})
    assert events == []
