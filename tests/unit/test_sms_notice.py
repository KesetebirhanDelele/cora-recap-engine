"""SMS = missed-call notification, email = program description; only the AI Systems Architect Accelerator is
ever offered (spec/37). Pure + mocked-LLM tests."""
from __future__ import annotations

import re
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from app.core import offer
from app.core import sms_gate as g
from app.core.ai_message_generator import (
    _load_brand_context,
    generate_vm_email,
    generate_vm_followup,
    generate_vm_sms,
)
from app.core.conversation_context import ConversationContext
from app.prompts.families import vm_followup_generator, vm_sms_notice  # noqa: F401
from app.prompts.registry import get_prompt

TERMS = offer.parse_terms(None)
TIERS = ("tier_1", "tier_2", "tier_3", "tier_final")


def _ctx(attempt=1, name="Sam"):
    return ConversationContext(contact_id="c1", campaign_name="New Lead", status="active", tier=None,
                               preferred_channel=None, lead_first_name=name, attempt_number=attempt)


class FakeClient:
    """Stands in for OpenAIClient; replays scripted JSON answers and records the messages it was given."""
    scripts: list[dict] = []
    seen: list[list[dict]] = []

    def __init__(self, settings=None, _client=None):
        pass

    def chat_completion(self, messages, model, response_format=None, max_tokens=None, **kw):
        FakeClient.seen.append(messages)
        return FakeClient.scripts.pop(0)


@pytest.fixture()
def llm():
    FakeClient.scripts, FakeClient.seen = [], []
    with patch("app.adapters.openai_client.OpenAIClient", FakeClient):
        yield FakeClient


# ── offer.py ──
def test_forbidden_terms_match_whole_words_only():
    assert offer.forbidden_hits("Learn about Data Analytics or AI", TERMS) == ["data analytics"]
    assert offer.forbidden_hits("our bootcamp and Power BI classes", TERMS) == ["bootcamp", "power bi"]
    assert offer.forbidden_hits("an excellent sequel, thanks", TERMS) == []          # excel / sql inside words
    assert offer.forbidden_hits("AI Systems Architect Accelerator", TERMS) == []
    assert offer.parse_terms("a, B ,") == ("a", "b")


@pytest.mark.parametrize("text", [
    "Our next class starts on Nov 12", "RSVP for the Open House", "Enroll today and save $500",
    "Watch their success story", "Sign up for the free class", "limited time offer"])
def test_marketing_wording_is_detected(text):
    assert offer.marketing_hit(text)


@pytest.mark.parametrize("text", [
    "Hi Sam, it's Cora from Colaberry. I tried to call about the AI Systems Architect Accelerator. "
    "What time works? Text STOP to stop alerts",
    "Sorry I missed you! Reply with a good time and I'll call you back. Text STOP to stop alerts"])
def test_notification_wording_is_clean(text):
    assert offer.marketing_hit(text) is None


# ── gate: notification-only + forbidden terms + 2 segments ──
def _cfg(**kw):
    return g.GateConfig(forbidden_terms=TERMS, **kw)


def test_followup_sms_is_notification_only_but_corrections_may_state_dates():
    promo = "Hi Sam, our next class starts on Nov 12 - RSVP for the Open House! Text STOP to stop alerts"
    assert g.decide(promo, g.GateUsage(), _cfg(notification_only=True)).action == g.BLOCK
    assert g.decide(promo, g.GateUsage(), _cfg(notification_only=False)).action == g.ALLOW   # correction / test SMS


def test_retired_course_name_is_blocked_in_every_sms():
    t = "Hi Sam, it's Cora. Interested in Data Analytics or AI? Text STOP to stop alerts"
    for notif in (True, False):
        d = g.decide(t, g.GateUsage(), _cfg(notification_only=notif))
        assert d.action == g.BLOCK and "no longer offer" in d.reason


def test_default_is_at_most_two_segments():
    assert g.GateConfig().max_segments_per_message == 2
    t = "word " * 70                                   # 350 chars -> 3 segments
    assert g.decide(t, g.GateUsage(), g.GateConfig()).action == g.BLOCK


def test_curly_punctuation_is_normalised_so_a_text_stays_two_segments():
    raw = "Hi Sam, it’s Cora from Colaberry — I tried calling. " + "a" * 120
    assert g.count_segments(raw) >= 3
    fixed = g.normalize_sms(raw)
    assert "’" not in fixed and "—" not in fixed and g.count_segments(fixed) == 2
    assert g.normalize_sms("Call me \U0001F600 ok") == "Call me ok"


# ── prompts ──
@pytest.mark.parametrize("family", ["vm_sms_notice", "vm_followup_generator"])
@pytest.mark.parametrize("version", TIERS)
def test_no_prompt_names_a_retired_course_or_uses_stories(family, version):
    brand = _load_brand_context(None, NS())
    entry = get_prompt(family, version)
    msgs = entry.build_messages(lead_first_name="Sam", campaign_name="AI training", prior_messages="(none)",
                                ghl_conversation_thread="(none)", **brand)
    system = msgs[0]["content"].format(brand_name="Colaberry", offer_name=brand["offer_name"],
                                       offer_facts=brand["offer_facts"])
    text = system + msgs[1]["content"]
    # retired names may appear only inside the email prompt's explicit "never mention" rule
    scrubbed = re.sub(r"- Offer the .*?\n- Do not use", "- Do not use", text, flags=re.S)
    assert not offer.forbidden_hits(scrubbed, TERMS), (family, version)
    assert "AI Systems Architect Accelerator" in text
    assert "video_transcripts" not in text and "story_link" not in text


def test_sms_prompt_forbids_links_dates_and_marketing_email_prompt_carries_the_facts():
    sms_sys = get_prompt("vm_sms_notice", "tier_1").system_prompt
    assert "NOT a marketing message" in sms_sys and "NEVER include" in sms_sys and "link" in sms_sys
    email_sys = get_prompt("vm_followup_generator", "tier_1").system_prompt
    assert "{offer_facts}" in email_sys and "success stories" in email_sys


def test_brand_context_defaults_to_the_one_offer():
    b = _load_brand_context(None, NS())
    assert b["offer_name"] == "AI Systems Architect Accelerator"
    assert "12-week" in b["offer_facts"] and "{free_url}" not in b["offer_facts"]


# ── generate_vm_sms ──
def test_sms_good_draft_is_returned_plain_and_ends_with_the_opt_out_line(llm):
    llm.scripts = [{"sms_text": "Hi Sam, it’s Cora from Colaberry. Sorry I missed you - what time works to talk?"}]
    out = generate_vm_sms(_ctx(), NS())
    assert out.endswith("Text STOP to stop alerts") and "’" not in out and len(out) <= 240
    assert g.count_segments(out) <= 2


def test_sms_with_a_retired_course_is_redrafted_once_then_accepted(llm):
    llm.scripts = [{"sms_text": "Hi Sam! Learn Data Analytics or AI with us. Text STOP to stop alerts"},
                   {"sms_text": "Hi Sam, it's Cora from Colaberry. I tried to call - when is a good time?"}]
    out = generate_vm_sms(_ctx(), NS())
    assert "Data Analytics" not in out and "good time" in out
    assert "rejected" in llm.seen[1][-1]["content"] and "no longer offer" in llm.seen[1][-1]["content"]


def test_sms_marketing_draft_twice_falls_back_to_a_safe_notice(llm):
    bad = {"sms_text": "Our class starts Nov 12! RSVP for the Open House at myfreeaiclass.com"}
    llm.scripts = [bad, bad]
    out = generate_vm_sms(_ctx(name="Sam"), NS())
    assert out.startswith("Hi Sam, it's Cora from Colaberry. I just tried to call you about the AI Systems Architect Accelerator")
    assert g.decide(out, g.GateUsage(), _cfg(notification_only=True)).action == g.ALLOW


def test_sms_llm_failure_falls_back(llm):
    llm.scripts = []                                    # pop from empty list raises
    out = generate_vm_sms(_ctx(name=None), NS())
    assert out.startswith("Hi there,") and out.endswith("Text STOP to stop alerts")


def test_long_draft_is_trimmed_to_240_keeping_the_opt_out_line(llm):
    llm.scripts = [{"sms_text": "Hi Sam, it's Cora from Colaberry. " + "I would love to talk when you have a minute. " * 8}]
    out = generate_vm_sms(_ctx(), NS())
    assert len(out) <= 240 and out.endswith("Text STOP to stop alerts")


def test_the_sms_prompt_changes_with_the_attempt(llm):
    for attempt, needle in ((1, "reached voicemail (a few minutes ago)"), (2, "called twice"), (4, "last automated text")):
        llm.scripts = [{"sms_text": "Hi Sam, it's Cora from Colaberry. I tried to call - when is a good time?"}]
        generate_vm_sms(_ctx(attempt), NS())
        assert needle in llm.seen[-1][1]["content"]


# ── generate_vm_email / routing ──
def test_email_with_a_retired_course_is_redrafted_then_falls_back(llm):
    good = {"email_subject": "Sorry I missed you", "preview_text": "p", "email_html": "<p>About the AI Systems Architect Accelerator.</p>",
            "email_text": "About the AI Systems Architect Accelerator."}
    bad = {**good, "email_html": "<p>Our Data Analytics bootcamp is great.</p>"}
    llm.scripts = [bad, good]
    assert generate_vm_email(_ctx(), NS())["email_subject"] == "Sorry I missed you"
    llm.scripts = [bad, bad]
    fb = generate_vm_email(_ctx(), NS())
    assert not offer.forbidden_hits(" ".join(fb.values()), TERMS) and fb["email_subject"]


def test_followup_requests_only_the_channel_it_needs(llm):
    llm.scripts = [{"sms_text": "Hi Sam, it's Cora from Colaberry. I tried to call - when is a good time?"}]
    r = generate_vm_followup(_ctx(), NS(), None, channel="sms")
    assert r.sms_text and r.email_subject == "" and len(llm.seen) == 1
    llm.scripts = [{"email_subject": "s", "preview_text": "", "email_html": "<p>x</p>", "email_text": "x"}]
    llm.seen.clear()
    r = generate_vm_followup(_ctx(), NS(), None, channel="email")
    assert r.sms_text == "" and r.email_subject == "s" and len(llm.seen) == 1


def test_employment_and_placement_claims_are_forbidden_in_every_message():
    from app.core import offer

    terms = offer.parse_terms(None)
    for claim in ("Our program has a 71% employment rate.", "We boast a high placement rate", "Job guarantee for graduates",
                  "A guaranteed job after week 12", "Great job placement support"):
        assert offer.forbidden_hits(claim, terms), claim
    assert offer.forbidden_hits("Colaberry helps learners prepare for AI roles.", terms) == []
