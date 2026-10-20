"""Banned-wording matcher: the standalone word "free" (TWC advertising rule, Ali 2026-10-04) and the "excel" verb false match
("skills needed to excel"). Pure logic, no DB, no LLM."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import content_audit as ca
from app.core import offer

TERMS = offer.parse_terms(None)          # the production default list (no app_config override exists)


@pytest.mark.parametrize("text", [
    "Start free today",
    "Create a free account",
    "Join the free Open House",
    "Learn it for free!",
    "A free\nclass is coming",                 # whitespace / newline between words
    "Try our risk-free trial",
    "FREE to join",
    "Free.",
])
def test_free_as_a_claim_is_flagged(text):
    assert "free" in offer.forbidden_hits(text, TERMS), text


@pytest.mark.parametrize("text", [
    "Feel free to reply with any question.",
    "feel   free to call us",                   # extra spaces collapse
    "FEEL FREE to ask",
    "Use your free time wisely",
    "Visit https://training.colaberry.com/start-free",              # the domain is not the word
    "freedom and carefree days",
    "$0 to start. No card needed. Membership from $149/mo.",
])
def test_free_in_a_non_claim_use_is_not_flagged(text):
    assert "free" not in offer.forbidden_hits(text, TERMS), text


@pytest.mark.parametrize("text", [
    "Skills needed to excel.",
    "You will excel at it",
    "Students who excel get noticed",
    "They excel in teams",
    "We can excel together",
    "An excellent program",                     # inside a longer word
])
def test_excel_as_a_verb_is_not_flagged(text):
    assert "excel" not in offer.forbidden_hits(text, TERMS), text


@pytest.mark.parametrize("text", [
    "Learn Excel and SQL",
    "Microsoft Excel course",
    "Advanced Excel spreadsheets",
    "Our Excel bootcamp",
    "Master into Excel basics",                 # "into" ends in "to" but is not the word "to"
    "Excel training starts Monday",
])
def test_excel_as_the_course_or_software_is_still_flagged(text):
    assert "excel" in offer.forbidden_hits(text, TERMS), text


def test_other_terms_are_unchanged():
    assert offer.forbidden_hits("our bootcamp and Power BI classes", TERMS) == ["bootcamp", "power bi"]
    assert offer.forbidden_hits("AI Systems Architect Accelerator", TERMS) == []


def test_free_is_a_hard_term_never_left_to_the_decline_judge():
    assert ca.is_hard_term("free") and ca.is_hard_term("placement rate")
    assert not ca.is_hard_term("bootcamp") and not ca.is_hard_term("freedom")
    assert ca.course_only(["data analytics", "bootcamp"]) is True
    assert ca.course_only(["data analytics", "free"]) is False       # a decline + "start free" must stay flagged
    assert ca.course_only(["free"]) is False


# ── Cora's own generated text must follow the same rule ──────────────────────────────────────────────────────────

def test_offer_facts_state_the_approved_wording_and_never_the_word_free_as_a_claim():
    facts = offer.DEFAULT_OFFER_FACTS.replace("{free_url}", "https://training.colaberry.com/start-free")
    assert "$0" in facts and "no card needed" in facts and "$149/mo" in facts
    allowed = re.sub(r"Never use the word 'free'.*?instead\.", "", facts, flags=re.S)       # the sentence that forbids it
    assert "free" not in offer.forbidden_hits(allowed, TERMS)


def test_followup_prompt_and_schedule_block_do_not_tell_the_model_to_say_free():
    from app.core.schedule_context import build_schedule_block

    block = build_schedule_block(None, None, None, "https://training.colaberry.com/start-free")
    assert "$0" in block and "free" not in offer.forbidden_hits(block, TERMS)
    src = Path("app/prompts/families/vm_followup_generator.py").read_text(encoding="utf-8")
    assert "the free start link" not in src and "start free" not in re.sub(r'\(not "free account".*?"start free"\)', "", src)


def test_date_correction_messages_use_the_approved_wording():
    src = Path("app/services/wrong_date_monitor.py").read_text(encoding="utf-8")
    assert "for free" not in src and "learning for $0" in src


# Approved $0 sign-up link (Kes 2026-10-20): its path contains "free" but must not be flagged.
@pytest.mark.parametrize("text", [
    "Start at https://training.colaberry.com/start-free today",
    "training.colaberry.com/start-free",
    "Sign up: HTTPS://WWW.TRAINING.COLABERRY.COM/START-FREE.",
    '<a href="https://training.colaberry.com/start-free?utm_source=email">https://training.colaberry.com/start-free</a>',
    "Start at https://training.colaberry.com/start-free/ now",
])
def test_approved_start_free_link_is_not_flagged(text):
    assert offer.forbidden_hits(text, TERMS) == []


@pytest.mark.parametrize("text", [
    "Start free at https://training.colaberry.com/start-free",                # the WORD outside the link still hits
    "https://training.colaberry.com/start-free-trial",                         # different path
    "https://other.example.com/start-free",                                    # other host
    "https://evil.com/?r=training.colaberry.com/start-free-x free",
])
def test_free_outside_the_approved_link_still_flagged(text):
    assert "free" in offer.forbidden_hits(text, TERMS), text
