"""
Unit tests for app.core.ghl_support_context.extract_support_context().
"""
from __future__ import annotations

from app.core.ghl_support_context import extract_support_context


def _contact(custom_fields: list[dict]) -> dict:
    return {"customFields": custom_fields}


def test_empty_contact_returns_empty_structure():
    result = extract_support_context(_contact([]))
    assert result == {"issue_categories": [], "issue_descriptions": [], "student_satisfaction": {}}


def test_extracts_primary_issue_category():
    contact = _contact([
        {"name": "What is your issue related to?", "value": "Billing"},
    ])
    result = extract_support_context(contact)
    assert result["issue_categories"] == ["Billing"]


def test_extracts_multiple_ticket_categories_deduped():
    contact = _contact([
        {"name": "What is your issue related to?", "value": "Billing"},
        {"name": "What is your issue related to? Ticket #1", "value": "Billing"},
        {"name": "What is your issue related to? Ticket #2", "value": "Technical"},
    ])
    result = extract_support_context(contact)
    assert result["issue_categories"] == ["Billing", "Technical"]


def test_multi_select_category_value_as_list():
    contact = _contact([
        {"name": "What is your issue related to?", "value": ["Billing", "Access"]},
    ])
    result = extract_support_context(contact)
    assert result["issue_categories"] == ["Billing", "Access"]


def test_extracts_issue_descriptions_across_ticket_slots():
    contact = _contact([
        {"name": "Support Issue", "value": "Can't access the classroom portal"},
        {"name": "Support Issue Ticket #1", "value": "Refund request for month 2"},
        {"name": "Support issue Ticket #3", "value": "Password reset needed"},
    ])
    result = extract_support_context(contact)
    assert result["issue_descriptions"] == [
        "Can't access the classroom portal",
        "Refund request for month 2",
        "Password reset needed",
    ]


def test_blank_description_excluded():
    contact = _contact([
        {"name": "Support Issue", "value": "   "},
        {"name": "Support Issue Ticket #1", "value": "Real issue text"},
    ])
    result = extract_support_context(contact)
    assert result["issue_descriptions"] == ["Real issue text"]


def test_extracts_satisfaction_survey_answers():
    contact = _contact([
        {"name": "1. How satisfied were you with the resolution of your issue?", "value": "Very satisfied"},
        {"name": "4. Did the support team resolve your issue in a timely manner?", "value": "Yes"},
    ])
    result = extract_support_context(contact)
    assert result["student_satisfaction"] == {
        "1. How satisfied were you with the resolution of your issue?": "Very satisfied",
        "4. Did the support team resolve your issue in a timely manner?": "Yes",
    }


def test_no_customfields_key_does_not_raise():
    result = extract_support_context({})
    assert result == {"issue_categories": [], "issue_descriptions": [], "student_satisfaction": {}}
