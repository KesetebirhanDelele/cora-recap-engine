"""
Unit tests for app.core.call_classification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.core.call_classification import (
    classify_from_known_signals,
    classify_from_transcript_ai,
    extract_classification_signals,
)

# ─────────────────────────────────────────────────────────────────────────────
# classify_from_known_signals — tags (checked first, per Kes 2026-08-25)
# ─────────────────────────────────────────────────────────────────────────────

def test_tag_containing_student_maps_to_support():
    result = classify_from_known_signals(tags=["data analytics student", "enrolled student"])
    assert result == ("support", "ghl_tags")


def test_tag_containing_enrolled_maps_to_support():
    result = classify_from_known_signals(tags=["enrolled"])
    assert result == ("support", "ghl_tags")


def test_tag_case_insensitive_match():
    result = classify_from_known_signals(tags=["Enrolled Student"])
    assert result == ("support", "ghl_tags")


def test_lead_disposition_tags_without_student_map_to_sales():
    result = classify_from_known_signals(
        tags=["not interested", "do not contact", "do not call again", "ai cold leads ii"]
    )
    assert result == ("sales", "ghl_tags")


def test_warm_lead_tag_maps_to_sales():
    result = classify_from_known_signals(tags=["school catalog request", "warm lead"])
    assert result == ("sales", "ghl_tags")


def test_tags_take_priority_over_picklist_fields():
    """Real data (2026-08-25): tags were populated when the picklist fields never were."""
    result = classify_from_known_signals(
        tags=["warm lead"], who_you_are_value="Current Student",
    )
    assert result == ("sales", "ghl_tags")


def test_empty_tag_list_falls_through_not_defaults_to_sales():
    """No tags at all is not the same claim as 'has tags but none say student' — must fall through."""
    result = classify_from_known_signals(tags=[], who_you_are_value="Current Student")
    assert result == ("support", "ghl_who_you_are")


def test_none_tags_falls_through():
    result = classify_from_known_signals(tags=None, enrollment_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result == ("support", "ghl_enrollment_date")


# ─────────────────────────────────────────────────────────────────────────────
# classify_from_known_signals — GHL picklist fields (fallback when no tags)
# ─────────────────────────────────────────────────────────────────────────────

def test_who_you_are_current_student_maps_to_support():
    result = classify_from_known_signals(who_you_are_value="Current Student")
    assert result == ("support", "ghl_who_you_are")


def test_who_you_are_potential_student_maps_to_sales():
    result = classify_from_known_signals(who_you_are_value="Potential Student")
    assert result == ("sales", "ghl_who_you_are")


def test_who_you_are_business_or_partner_maps_to_other():
    result = classify_from_known_signals(who_you_are_value="Business or Partner")
    assert result == ("other", "ghl_who_you_are")


def test_falls_back_to_select_option_1_when_who_you_are_empty():
    result = classify_from_known_signals(select_option_1_value="Current Student")
    assert result == ("support", "ghl_select_option_1")


def test_falls_back_to_select_option_2_when_others_empty():
    result = classify_from_known_signals(select_option_2_value="Potential Student")
    assert result == ("sales", "ghl_select_option_2")


def test_who_you_are_takes_priority_over_select_option_1():
    result = classify_from_known_signals(
        who_you_are_value="Current Student", select_option_1_value="Potential Student",
    )
    assert result == ("support", "ghl_who_you_are")


def test_blank_string_treated_as_empty():
    result = classify_from_known_signals(who_you_are_value="   ", enrollment_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result == ("support", "ghl_enrollment_date")


def test_unrecognized_picklist_value_falls_through():
    result = classify_from_known_signals(
        who_you_are_value="Some Unexpected Value",
        enrollment_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert result == ("support", "ghl_enrollment_date")


def test_enrollment_date_used_when_no_picklist_value():
    result = classify_from_known_signals(enrollment_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result == ("support", "ghl_enrollment_date")


def test_call_history_used_when_no_picklist_or_enrollment_date():
    result = classify_from_known_signals(has_enrolled_call_history=True)
    assert result == ("support", "call_history")


def test_priority_order_picklist_beats_enrollment_date():
    result = classify_from_known_signals(
        who_you_are_value="Potential Student",
        enrollment_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert result == ("sales", "ghl_who_you_are")


def test_priority_order_enrollment_date_beats_call_history():
    result = classify_from_known_signals(
        enrollment_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        has_enrolled_call_history=True,
    )
    assert result == ("support", "ghl_enrollment_date")


def test_returns_unknown_when_nothing_resolves():
    result = classify_from_known_signals()
    assert result == ("unknown", "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# classify_from_transcript_ai
# ─────────────────────────────────────────────────────────────────────────────

def test_ai_classification_returns_sales():
    client = MagicMock()
    client.chat_completion.return_value = {"conversation_type": "sales", "reasoning": "discussing enrollment"}

    result = classify_from_transcript_ai("some transcript", client)

    assert result == ("sales", "ai_inferred")


def test_ai_classification_returns_support():
    client = MagicMock()
    client.chat_completion.return_value = {"conversation_type": "support", "reasoning": "billing issue on active account"}

    result = classify_from_transcript_ai("some transcript", client)

    assert result == ("support", "ai_inferred")


def test_ai_classification_unexpected_value_defaults_unknown():
    client = MagicMock()
    client.chat_completion.return_value = {"conversation_type": "banana"}

    result = classify_from_transcript_ai("some transcript", client)

    assert result == ("unknown", "ai_inferred")


def test_ai_classification_handles_openai_error_gracefully():
    from app.adapters.openai_client import OpenAIError

    client = MagicMock()
    client.chat_completion.side_effect = OpenAIError("boom")

    result = classify_from_transcript_ai("some transcript", client)

    assert result == ("unknown", "ai_inferred")


def test_ai_classification_handles_missing_key_gracefully():
    client = MagicMock()
    client.chat_completion.return_value = {}  # no "conversation_type" key

    result = classify_from_transcript_ai("some transcript", client)

    assert result == ("unknown", "ai_inferred")


def test_ai_classification_truncates_long_transcript():
    client = MagicMock()
    client.chat_completion.return_value = {"conversation_type": "sales"}
    long_transcript = "x" * 20000

    classify_from_transcript_ai(long_transcript, client)

    sent_content = client.chat_completion.call_args.kwargs["messages"][0]["content"]
    assert len(sent_content) < 20000


# ─────────────────────────────────────────────────────────────────────────────
# extract_classification_signals
# ─────────────────────────────────────────────────────────────────────────────

def _contact(custom_fields: list[dict]) -> dict:
    return {"customFields": custom_fields}


def test_extract_signals_empty_contact():
    result = extract_classification_signals(_contact([]))
    assert result == {
        "tags": [],
        "who_you_are_value": None,
        "select_option_1_value": None,
        "select_option_2_value": None,
        "enrollment_date": None,
    }


def test_extract_signals_pulls_tags():
    contact = {"customFields": [], "tags": ["warm lead", "school catalog request"]}
    result = extract_classification_signals(contact)
    assert result["tags"] == ["warm lead", "school catalog request"]


def test_extract_signals_no_tags_key_returns_empty_list():
    contact = {"customFields": []}
    result = extract_classification_signals(contact)
    assert result["tags"] == []


def test_extract_signals_strips_blank_tags():
    contact = {"customFields": [], "tags": ["warm lead", "  ", ""]}
    result = extract_classification_signals(contact)
    assert result["tags"] == ["warm lead"]


def test_extract_who_you_are_by_name():
    contact = _contact([{"name": "Who you are", "value": "Current Student"}])
    result = extract_classification_signals(contact)
    assert result["who_you_are_value"] == "Current Student"


def test_extract_disambiguates_duplicate_select_option_fields_by_field_key():
    """
    Two fields share the identical display name "Select an option that best
    describes you" — only fieldKey tells them apart.
    """
    contact = _contact([
        {
            "name": "Select an option that best describes you",
            "fieldKey": "contact.select_an_option_that_best_describes_you",
            "value": "Potential Student",
        },
        {
            "name": "Select an option that best describes you",
            "fieldKey": "contact.select_an_option_that_best_describes_you2",
            "value": "Current Student",
        },
    ])
    result = extract_classification_signals(contact)
    assert result["select_option_1_value"] == "Potential Student"
    assert result["select_option_2_value"] == "Current Student"


def test_extract_enrollment_date_parses_iso_string():
    contact = _contact([{"name": "Enrollment Date", "value": "2026-03-15T00:00:00.000Z"}])
    result = extract_classification_signals(contact)
    assert result["enrollment_date"].year == 2026
    assert result["enrollment_date"].month == 3
    assert result["enrollment_date"].day == 15


def test_extract_enrollment_date_unparseable_returns_none_not_raise():
    contact = _contact([{"name": "Enrollment Date", "value": "not-a-date"}])
    result = extract_classification_signals(contact)
    assert result["enrollment_date"] is None


def test_extract_list_value_takes_first_element():
    contact = _contact([{"name": "Who you are", "value": ["Current Student"]}])
    result = extract_classification_signals(contact)
    assert result["who_you_are_value"] == "Current Student"


def test_extract_blank_value_treated_as_none():
    contact = _contact([{"name": "Who you are", "value": "   "}])
    result = extract_classification_signals(contact)
    assert result["who_you_are_value"] is None
