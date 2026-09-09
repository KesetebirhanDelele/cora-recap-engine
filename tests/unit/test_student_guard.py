"""
Unit tests for app.core.student_guard.check_is_student() (spec/27).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _settings():
    return SimpleNamespace(ghl_base_url="https://example.invalid", ghl_timeout_seconds=1)


def _patch_ghl(contact: dict, *, found: bool = True):
    """Patch GHLClient's two read methods (leaving __init__ and the real
    static get_field_value helper that call_classification uses intact)."""
    return patch.multiple(
        "app.adapters.ghl.GHLClient",
        search_contact_by_phone=MagicMock(
            return_value={"id": "ghl-123"} if found else None
        ),
        get_contact=MagicMock(return_value={"contact": contact}),
    )


@pytest.mark.parametrize("tag", ["enrolled student", "ipbc student", "Data Analytics Student"])
def test_student_tag_is_flagged(tag):
    from app.core.student_guard import check_is_student

    with _patch_ghl({"tags": [tag]}):
        result = check_is_student("+15551230000", _settings())

    assert result is not None
    assert result["ghl_contact_id"] == "ghl-123"
    assert result["classification_source"] == "ghl_tags"
    assert result["matched_tags"] == [tag]


def test_registered_not_enrolled_is_not_a_student():
    """spec/23's keyword rule matches "enrolled" as a substring; a lead tagged
    "registered - not enrolled" is a sales target, not a student."""
    from app.core.student_guard import check_is_student

    with _patch_ghl({"tags": ["registered - not enrolled"]}):
        assert check_is_student("+15551230001", _settings()) is None


def test_mixed_tags_with_one_real_student_tag_is_flagged():
    from app.core.student_guard import check_is_student

    with _patch_ghl({"tags": ["registered - not enrolled", "enrolled student"]}):
        result = check_is_student("+15551230002", _settings())

    assert result is not None
    assert "enrolled student" in result["matched_tags"]


def test_current_student_picklist_is_flagged_without_a_tag():
    from app.core.student_guard import check_is_student

    contact = {
        "tags": [],
        "customFields": [{"name": "Who you are", "value": "Current Student"}],
    }
    with _patch_ghl(contact):
        result = check_is_student("+15551230003", _settings())

    assert result is not None
    assert result["classification_source"] == "ghl_who_you_are"
    assert result["matched_tags"] == []


def test_lead_tag_only_is_not_a_student():
    from app.core.student_guard import check_is_student

    with _patch_ghl({"tags": ["cold lead", "ai lead"]}):
        assert check_is_student("+15551230004", _settings()) is None


def test_contact_not_found_returns_none():
    from app.core.student_guard import check_is_student

    with _patch_ghl({"tags": ["enrolled student"]}, found=False):
        assert check_is_student("+15551230005", _settings()) is None


def test_empty_phone_returns_none_without_touching_ghl():
    from app.core.student_guard import check_is_student

    with patch("app.adapters.ghl.GHLClient.search_contact_by_phone") as mock_search:
        assert check_is_student("", _settings()) is None
        mock_search.assert_not_called()


def test_ghl_error_fails_open(caplog):
    from app.core.student_guard import check_is_student

    with patch(
        "app.adapters.ghl.GHLClient.search_contact_by_phone",
        side_effect=RuntimeError("GHL 503"),
    ):
        assert check_is_student("+15551230006", _settings()) is None
    assert "failing open" in caplog.text
