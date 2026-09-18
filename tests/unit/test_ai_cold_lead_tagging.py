"""
Tests for app/services/ai_cold_lead_tagging.py (spec/31).

Covers:
  1. build_search_filters — correct fields/operators, exclusion-tag parsing/trimming
  2. fetch_candidate_contacts — pagination across pages via searchAfter cursor
  3. fetch_candidate_contacts — respects the per-run batch cap
  4. run_tagging_cycle — disabled flag: skips without calling GHL at all
  5. run_tagging_cycle — happy path: all candidates tagged
  6. run_tagging_cycle — idempotency: already-tagged contact is skipped, not re-tagged
  7. run_tagging_cycle — per-contact failure isolation (one bad contact doesn't abort the cycle)
  8. run_tagging_cycle — batch cap enforcement end-to-end
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.adapters.ghl import GHLError
from app.services.ai_cold_lead_tagging import (
    build_search_filters,
    fetch_candidate_contacts,
    run_tagging_cycle,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        ai_cold_lead_tagging_tag="ai cold leads",
        ai_cold_lead_tagging_exclude_tags="warm lead, spam ,international lead",
        ai_cold_lead_tagging_lookback_days=30,
        ai_cold_lead_tagging_batch_cap=500,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _flags(enabled: bool = True, writes_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(ai_cold_lead_tagging_enabled=enabled, ghl_writes_enabled=writes_enabled)


def _contact(id_: str, tags: list[str] | None = None) -> dict:
    return {"id": id_, "tags": tags or [], "searchAfter": [1, id_]}


# ─────────────────────────────────────────────────────────────────────────────
# 1. build_search_filters
# ─────────────────────────────────────────────────────────────────────────────

def test_build_search_filters_fields_and_operators():
    filters = build_search_filters(_settings())
    by_field = {f["field"]: f for f in filters}

    assert by_field["type"] == {"field": "type", "operator": "eq", "value": "lead"}
    assert by_field["dnd"] == {"field": "dnd", "operator": "eq", "value": False}
    assert by_field["phone"] == {"field": "phone", "operator": "wildcard", "value": "+1*"}
    assert by_field["lastActivity"]["operator"] == "range"
    assert by_field["lastActivity"]["value"]["gte"] == 0
    assert isinstance(by_field["lastActivity"]["value"]["lte"], int)


def test_build_search_filters_trims_and_parses_exclude_tags():
    filters = build_search_filters(_settings(ai_cold_lead_tagging_exclude_tags="warm lead, spam ,international lead"))
    tags_filter = next(f for f in filters if f["field"] == "tags")
    assert tags_filter["operator"] == "not_contains"
    assert tags_filter["value"] == ["warm lead", "spam", "international lead"]


# ─────────────────────────────────────────────────────────────────────────────
# 2-3. fetch_candidate_contacts
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_candidate_contacts_paginates_via_search_after():
    settings = _settings()
    client = MagicMock()
    page1 = {"contacts": [_contact(f"c{i}") for i in range(100)]}
    page2 = {"contacts": [_contact("c100")]}
    client.search_contacts.side_effect = [page1, page2]

    contacts = list(fetch_candidate_contacts(client, settings))

    assert len(contacts) == 101
    assert client.search_contacts.call_count == 2
    second_call_kwargs = client.search_contacts.call_args_list[1].kwargs
    assert second_call_kwargs["search_after"] == [1, "c99"]


def test_fetch_candidate_contacts_respects_batch_cap():
    settings = _settings(ai_cold_lead_tagging_batch_cap=2)
    client = MagicMock()
    client.search_contacts.return_value = {
        "contacts": [_contact("c1"), _contact("c2"), _contact("c3")]
    }

    contacts = list(fetch_candidate_contacts(client, settings))

    assert [c["id"] for c in contacts] == ["c1", "c2"]
    client.search_contacts.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 4-8. run_tagging_cycle
# ─────────────────────────────────────────────────────────────────────────────

def test_run_tagging_cycle_disabled_flag_skips_without_calling_ghl():
    session = MagicMock()
    mock_client_cls = MagicMock()

    with patch("app.core.mode_flags.get_mode_flags", return_value=_flags(enabled=False)), \
         patch("app.adapters.ghl.GHLClient", mock_client_cls):
        run = run_tagging_cycle(session, _settings())

    assert run.status == "skipped"
    assert run.dry_run is True
    mock_client_cls.assert_not_called()


def test_run_tagging_cycle_happy_path_tags_all_contacts():
    session = MagicMock()
    mock_client = MagicMock()
    mock_client.search_contacts.side_effect = [
        {"contacts": [_contact("c1"), _contact("c2"), _contact("c3")]},
    ]
    mock_client.add_contact_tag.return_value = {"succeeded": True}
    mock_client_cls = MagicMock(return_value=mock_client)

    with patch("app.core.mode_flags.get_mode_flags", return_value=_flags()), \
         patch("app.adapters.ghl.GHLClient", mock_client_cls):
        run = run_tagging_cycle(session, _settings())

    assert run.status == "completed"
    assert run.dry_run is False
    assert run.contacts_scanned == 3
    assert run.contacts_tagged == 3
    assert run.contacts_failed == 0
    assert mock_client.add_contact_tag.call_count == 3
    mock_client.close.assert_called_once()


def test_run_tagging_cycle_skips_already_tagged_contact():
    session = MagicMock()
    mock_client = MagicMock()
    mock_client.search_contacts.side_effect = [
        {"contacts": [_contact("c1", tags=["AI Cold Leads"]), _contact("c2")]},
    ]
    mock_client.add_contact_tag.return_value = {"succeeded": True}
    mock_client_cls = MagicMock(return_value=mock_client)

    with patch("app.core.mode_flags.get_mode_flags", return_value=_flags()), \
         patch("app.adapters.ghl.GHLClient", mock_client_cls):
        run = run_tagging_cycle(session, _settings())

    assert run.contacts_scanned == 2
    assert run.contacts_skipped_already_tagged == 1
    assert run.contacts_tagged == 1
    mock_client.add_contact_tag.assert_called_once()
    call_args = mock_client.add_contact_tag.call_args
    assert call_args.args[:2] == ("c2", "ai cold leads")


def test_run_tagging_cycle_isolates_per_contact_failure():
    session = MagicMock()
    mock_client = MagicMock()
    mock_client.search_contacts.side_effect = [
        {"contacts": [_contact("c1"), _contact("c2")]},
    ]
    mock_client.add_contact_tag.side_effect = [GHLError("boom"), {"succeeded": True}]
    mock_client_cls = MagicMock(return_value=mock_client)

    with patch("app.core.mode_flags.get_mode_flags", return_value=_flags()), \
         patch("app.adapters.ghl.GHLClient", mock_client_cls):
        run = run_tagging_cycle(session, _settings())

    assert run.status == "completed"
    assert run.contacts_scanned == 2
    assert run.contacts_tagged == 1
    assert run.contacts_failed == 1


def test_run_tagging_cycle_respects_batch_cap_end_to_end():
    session = MagicMock()
    mock_client = MagicMock()
    mock_client.search_contacts.return_value = {
        "contacts": [_contact("c1"), _contact("c2")]
    }
    mock_client.add_contact_tag.return_value = {"succeeded": True}
    mock_client_cls = MagicMock(return_value=mock_client)

    with patch("app.core.mode_flags.get_mode_flags", return_value=_flags()), \
         patch("app.adapters.ghl.GHLClient", mock_client_cls):
        run = run_tagging_cycle(session, _settings(ai_cold_lead_tagging_batch_cap=1))

    assert run.contacts_scanned == 1
    assert run.contacts_tagged == 1
