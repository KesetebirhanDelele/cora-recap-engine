"""Meaning check after the word filter, for ALL word lists (Kes 2026-11-09). No DB, no real LLM.

Rules under test: the judge reads courses, employment claims and "free"; it can only REMOVE a flag; both reads must agree;
a flag that stays carries both results; a cleared message leaves a 'content_ok' record and no alert line.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core import content_audit as ca
from app.core import offer
from app.services import channel_health as ch
from app.services import content_judge as cj
from app.services import delivery_sync as ds

NOW = datetime(2026, 11, 9, 23, 21, tzinfo=timezone.utc)
DISCLAIMER = ("The Explorer plan needs $0 to start. The certification helps you prepare for AI roles, "
              "but Colaberry does not guarantee job placement. Let me know if you want details on how to get started!")


@pytest.fixture(autouse=True)
def _clear():
    cj._cache.clear()
    cj._detail.clear()
    yield
    cj._cache.clear()
    cj._detail.clear()


# ── word filter + categories ────────────────────────────────────────────────
def test_the_flagged_message_matches_on_job_placement_only():
    hits = offer.forbidden_hits(DISCLAIMER, offer.parse_terms(None))
    assert hits == ["job placement"]                      # not "free", not "AI roles"


def test_preparing_for_ai_roles_does_not_trip_the_word_filter_at_all():
    assert offer.forbidden_hits("The certification helps you prepare for AI roles and AI careers.", offer.parse_terms(None)) == []


def test_categories_split_the_three_word_lists():
    assert ca.categories(["bootcamp", "sql"]) == {"course"}
    assert ca.categories(["job placement"]) == {"employment"}
    assert ca.categories(["free"]) == {"free"}
    assert ca.categories(["bootcamp", "free", "placement rate"]) == {"course", "free", "employment"}


# ── prompts per category ────────────────────────────────────────────────────
def test_course_only_keeps_the_original_prompt():
    assert cj.prompt_for({"course"}) == cj._PROMPT and cj.prompt_for(set()) == cj._PROMPT


def test_employment_prompt_clears_denials_and_soft_preparation_but_not_promises():
    p = cj.prompt_for({"employment"})
    assert "RULE employment" in p and "does not guarantee job placement" in p and "prepare for AI roles" in p
    assert "RULE free" not in p and "RULE retired course" not in p


def test_free_prompt_and_mixed_prompt():
    assert "RULE free" in cj.prompt_for({"free"}) and "feel free to reply" in cj.prompt_for({"free"})
    mixed = cj.prompt_for({"course", "employment", "free"})
    assert all(r in mixed for r in ("RULE retired course", "RULE employment", "RULE free"))


def test_the_judge_is_given_the_prompt_for_the_flagged_category():
    c = MagicMock()
    c.chat_completion.return_value = {"verdict": "not_offering", "reason": "only a denial"}
    with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
        v = cj.judge(SimpleNamespace(), "m1", DISCLAIMER, "Does the certification help for employment?", terms=["job placement"])
    assert v == cj.NOT_OFFERING
    system = c.chat_completion.call_args_list[0].args[0][0]["content"]
    assert "RULE employment" in system
    d = cj.last_detail("m1")
    assert d["votes"] == ["not_offering", "not_offering"] and d["reason"] == "only a denial"


def test_split_error_or_offers_never_clear_an_employment_flag():
    for outs, want in [(({"verdict": "not_offering"}, {"verdict": "unclear"}), cj.UNCLEAR),
                       (({"verdict": "offers"}, {"verdict": "not_offering"}), cj.OFFERS),
                       (({"verdict": "not_offering"}, RuntimeError("boom")), cj.UNCLEAR)]:
        cj._cache.clear()
        c = MagicMock()
        c.chat_completion.side_effect = list(outs)
        with patch("app.adapters.openai_client.OpenAIClient", return_value=c):
            assert cj.judge(SimpleNamespace(), "mx", "We help you land a job.", None, terms=["job placement"]) == want


# ── wiring: cleared = no alert, record kept; kept = alert with both results ─
def _run(body, verdict, detail=None):
    flags, oks = [], []

    def fake_judge(settings, message_id, text, prior, terms=None):
        cj._detail[message_id] = detail or {"verdict": verdict, "votes": [verdict, verdict], "reason": "why"}
        return verdict

    def write(*a, **kw):
        (oks if kw.get("kind") in ("content_ok", "staff_seen") else flags).append(kw)

    with patch.object(ds, "_upsert_event", write), patch.object(ds, "_contact_email", lambda s, c: None), \
            patch("app.services.content_judge.judge", fake_judge), \
            patch("app.core.app_config.get_config_value", lambda key, s, st, default=None: default):
        ds._flag_content(None, SimpleNamespace(), {"id": "m1", "body": body, "source": "app"}, "sms", "c1", NOW, NOW, conv_msgs=[])
    return flags, oks


def test_the_disclaimer_is_cleared_with_no_alert_and_a_record_with_both_results():
    flags, oks = _run(DISCLAIMER, cj.NOT_OFFERING)
    assert flags == []                                                   # no content_flag row -> no alert, no email
    ok = next(e for e in oks if e["kind"] == "content_ok")
    assert ok["detail"]["terms"] == ["job placement"] and ok["detail"]["votes"] == ["not_offering", "not_offering"]
    assert ok["detail"]["reason"] == "why"


@pytest.mark.parametrize("body,verdict", [
    ("Finish the program and we will help you land a job. Our placement rate is 71%.", cj.OFFERS),
    ("Our job placement is excellent.", cj.UNCLEAR),
    ("The Explorer plan is free to start.", cj.OFFERS),
])
def test_real_claims_stay_flagged_and_carry_both_results(body, verdict):
    flags, oks = _run(body, verdict)
    assert len(flags) == 1 and not [o for o in oks if o["kind"] == "content_ok"]
    d = flags[0]["detail"]
    assert d["llm_verdict"] == verdict and d["llm_votes"] == [verdict, verdict] and d["llm_reason"] == "why"


def test_a_flag_without_a_meaning_check_says_so():
    line = ch._checks_text({"terms": ["answered_opt_out"]}, "answered_opt_out")
    assert line == "word filter: answered_opt_out | meaning check: not run"


def test_alert_line_shows_both_checks_when_the_flag_is_kept():
    detail = {"llm_verdict": "offers", "llm_votes": ["offers", "not_offering"], "llm_reason": "states a placement rate"}
    line = ch._checks_text(detail, "placement rate")
    assert line.startswith("word filter: placement rate | meaning check: a rule is broken (1 of 2 reads said fine)")
    assert "states a placement rate" in line and line.endswith("kept flagged")


def test_flag_line_uses_the_checks_text_for_texts_and_voice():
    detail = {"llm_verdict": "unclear", "llm_votes": ["unclear", "unclear"], "snippet": "x", "origin": "GHL AI Receptionist (voice call)"}
    with patch.object(ch, "_lead_line", lambda s, c: "Lead"):
        sms = ch._flag_line(None, ("m9", "c1", "sms", "job placement", "app", NOW, detail))
        voice = ch._flag_line(None, ("call9", "c1", "voice", "free", "ghl_voice_ai", NOW, detail))
    assert "word filter: job placement | meaning check: could not tell" in sms
    assert "word filter: free | meaning check: could not tell" in voice
