"""Widened employment words for the audit's word check (Ali, approved 2026-11-11). Pure logic, no I/O."""
from __future__ import annotations

import pytest

from app.core import content_audit as ca
from app.core import offer

BASE = offer.parse_terms(None)
AUDIT = ca.audit_terms(BASE)


@pytest.mark.parametrize("text,words", [
    ("we don't guarantee placement, but everyone gets hired", ["placement", "hired"]),     # the example Ali raised: was invisible before
    ("The certification helps you prepare for AI roles, but we do not guarantee jobs.", ["jobs"]),
    ("We cannot guarantee a job after the program.", ["guarantee a job"]),
    ("We are hiring a tutor.", ["hiring"]),
    ("Companies hire our graduates.", ["hire"]),
    ("Graduates are hired by top companies.", ["hired"]),
    ("Our placement team can help.", ["placement"]),
])
def test_each_approved_word_is_flagged(text, words):
    assert ca.forbidden_in(text, AUDIT) == words


@pytest.mark.parametrize("text", [
    "This 12-week program is designed to fit around your full-time job.",   # bare "job": the sentence behind 254 of 256 test-week flags
    "We would like to schedule your job interview on Friday.",
    "Your orientation is the first Monday of the month at 6:30 PM Central.",
    "Colaberry helps learners prepare for AI roles.",
    "Rehired and chilly are not the word.",                                    # whole words only
])
def test_text_without_an_approved_word_is_not_flagged(text):
    assert ca.forbidden_in(text, AUDIT) == []


def test_the_old_list_missed_the_example_which_is_the_gap_being_closed():
    assert ca.forbidden_in("we don't guarantee placement, but everyone gets hired", BASE) == []
    assert ca.forbidden_in("Colaberry does not guarantee job placement.", BASE) == ["job placement"]


def test_the_message_generation_gate_is_not_widened():
    assert not {"placement", "hire", "hired", "hiring", "jobs", "guarantee a job"} & set(BASE)


def test_audit_terms_keeps_existing_terms_adds_extras_once_and_can_be_overridden():
    assert AUDIT[:len(BASE)] == BASE
    assert [t for t in AUDIT if t == "placement"] == ["placement"]
    assert ca.audit_terms(BASE, "") == BASE                                  # "" switches the extras off
    assert ca.audit_terms(BASE, "salary") == BASE + ("salary",)
    assert ca.audit_terms(("placement",), "placement,hired") == ("placement", "hired")      # no duplicate


@pytest.mark.parametrize("term,employment", [
    ("placement", True), ("hire", True), ("hired", True), ("hiring", True), ("jobs", True), ("guarantee a job", True),
    ("job placement", True), ("employment rate", True),
    ("free", False), ("sql", False), ("data analytics", False),
])
def test_new_words_are_judged_under_the_employment_rules(term, employment):
    assert ca.is_employment_term(term) is employment
    assert (ca.EMPLOYMENT in ca.categories([term])) is employment
