"""Unit tests for app.core.explorer_guard (spec/51): Explorer sign-ups are never dialed by Cora's AI campaigns."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.explorer_guard import check_is_explorer, exclusion_tags, matching_tags


def _settings(enabled=True, tags="ai explorer"):
    return SimpleNamespace(explorer_call_exclusion_enabled=enabled, explorer_call_exclusion_tags=tags,
                           ghl_base_url="https://example.invalid", ghl_timeout_seconds=1)


def _ghl(contact, *, found=True):
    return patch.multiple(
        "app.adapters.ghl.GHLClient",
        search_contact_by_phone=MagicMock(return_value={"id": "ghl-1"} if found else None),
        get_contact=MagicMock(return_value={"contact": contact}),
    )


def test_tag_list_is_lowercased_trimmed_and_ignores_blanks():
    assert exclusion_tags(_settings(tags=" AI Explorer , other tag ,, ")) == frozenset({"ai explorer", "other tag"})
    assert exclusion_tags(_settings(tags="")) == frozenset()


def test_matching_is_case_insensitive_and_keeps_original_spelling():
    assert matching_tags(["AI Explorer", "left voicemail"], frozenset({"ai explorer"})) == ["AI Explorer"]
    assert matching_tags(None, frozenset({"ai explorer"})) == []
    assert matching_tags([None, 5, "x"], frozenset({"ai explorer"})) == []


def test_contact_with_the_tag_is_flagged():
    with _ghl({"tags": ["left voicemail", "ai explorer"]}):
        r = check_is_explorer("+15551230000", _settings())
    assert r == {"ghl_contact_id": "ghl-1", "matched_tags": ["ai explorer"]}


@pytest.mark.parametrize("tags", [[], ["left voicemail", "ent-priority-call"], ["explorer"], ["ai explorer II"]])
def test_contact_without_the_exact_tag_is_not_flagged(tags):
    with _ghl({"tags": tags}):
        assert check_is_explorer("+15551230000", _settings()) is None


def test_unknown_contact_is_not_flagged():
    with _ghl({}, found=False):
        assert check_is_explorer("+15551230000", _settings()) is None


def test_switch_off_or_no_tags_or_no_phone_means_no_lookup_at_all():
    with patch("app.adapters.ghl.GHLClient") as client:
        assert check_is_explorer("+15551230000", _settings(enabled=False)) is None
        assert check_is_explorer("+15551230000", _settings(tags="")) is None
        assert check_is_explorer("", _settings()) is None
        client.assert_not_called()


def test_ghl_failure_fails_open_and_does_not_raise():
    with patch("app.adapters.ghl.GHLClient", side_effect=RuntimeError("GHL down")):
        assert check_is_explorer("+15551230000", _settings()) is None


def test_real_settings_default_is_on_with_the_ai_explorer_tag():
    from app.config.settings import Settings

    f = Settings.model_fields
    assert f["explorer_call_exclusion_enabled"].default is True
    assert f["explorer_call_exclusion_tags"].default == "ai explorer"
