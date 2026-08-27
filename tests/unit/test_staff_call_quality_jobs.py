"""
Unit tests for app.worker.jobs.staff_call_quality_jobs.

Covers the per-item processing logic (_process_call_message), dedupe
(_filter_unprocessed), and the call-history cross-check
(_has_enrolled_call_history). The full claim/run/reschedule entrypoint
lifecycle is exercised elsewhere in this repo's pattern (see
test_webhook_recovery_jobs equivalents) and is intentionally not
re-duplicated here — this file focuses on the new logic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.adapters.ghl import GHLError
from app.models.base import Base
from app.models.call_event import CallEvent
from app.models.lead_state import LeadState
from app.models.staff_call_quality import StaffCallQuality
from app.worker.jobs.staff_call_quality_jobs import (
    _discover_conversations,
    _filter_unprocessed,
    _has_enrolled_call_history,
    _parse_ghl_datetime,
    _process_call_message,
    _run_scan_cycle,
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


def _connected_call_message(**overrides) -> dict:
    base = {
        "id": "msg-1",
        "direction": "outbound",
        "userId": "rep-1",
        "dateAdded": "2026-04-29T23:03:35.210Z",
        "meta": {"call": {"duration": 49, "status": "completed"}},
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# _run_scan_cycle — off-by-default gate
# ─────────────────────────────────────────────────────────────────────────────

def test_scan_cycle_noop_when_disabled(session):
    """
    Deploying this job must not silently start scanning + spending OpenAI
    credits across the whole GHL location without an explicit opt-in.
    """
    settings = MagicMock()
    settings.staff_call_quality_scan_enabled = False

    with patch("app.adapters.ghl.GHLClient") as MockGHL:
        _run_scan_cycle(session, settings)
        MockGHL.assert_not_called()


def test_scan_cycle_proceeds_when_enabled(session):
    settings = MagicMock()
    settings.staff_call_quality_scan_enabled = True

    with patch("app.adapters.ghl.GHLClient") as MockGHL:
        mock_client = MagicMock()
        mock_client.search_conversations.return_value = []
        MockGHL.return_value = mock_client

        _run_scan_cycle(session, settings)

        MockGHL.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# _discover_conversations — pagination direction + no last_message_type filter
# (spec/23: fixes the note-masking gap and the backward-pagination bug)
# ─────────────────────────────────────────────────────────────────────────────

def _conv(conv_id: str, last_message_date: int) -> dict:
    return {"id": conv_id, "contactId": f"contact-{conv_id}", "lastMessageDate": last_message_date}


def test_discover_stops_on_short_page():
    """A page shorter than the page size means we've caught up to 'now' — no second call."""
    mock_client = MagicMock()
    mock_client.search_conversations.return_value = [_conv("c1", 1000), _conv("c2", 2000)]

    result = _discover_conversations(mock_client)

    assert [c["id"] for c in result] == ["c1", "c2"]
    assert mock_client.search_conversations.call_count == 1


def test_discover_does_not_filter_by_last_message_type():
    """The old filter dropped a call the moment a rep's note became the most recent message."""
    mock_client = MagicMock()
    mock_client.search_conversations.return_value = []

    _discover_conversations(mock_client)

    call_kwargs = mock_client.search_conversations.call_args[1]
    assert "last_message_type" not in call_kwargs


def test_discover_pages_forward_ascending():
    """Pagination must request ascending sort — GHL's default (descending) walks backward in time."""
    mock_client = MagicMock()
    mock_client.search_conversations.return_value = []

    _discover_conversations(mock_client)

    call_kwargs = mock_client.search_conversations.call_args[1]
    assert call_kwargs["sort_by"] == "last_message_date"
    assert call_kwargs["sort"] == "asc"


def test_discover_advances_cursor_using_last_item_date():
    """A full page (== page size) means there may be more — next call's cursor is the last item's date."""
    from app.worker.jobs import staff_call_quality_jobs as m

    full_page = [_conv(f"c{i}", 1000 + i) for i in range(m._DISCOVERY_PAGE_SIZE)]
    short_page = [_conv("last", 99999)]
    mock_client = MagicMock()
    mock_client.search_conversations.side_effect = [full_page, short_page]

    result = _discover_conversations(mock_client)

    assert mock_client.search_conversations.call_count == 2
    second_call_kwargs = mock_client.search_conversations.call_args_list[1][1]
    assert second_call_kwargs["start_after_date"] == full_page[-1]["lastMessageDate"]
    assert len(result) == m._DISCOVERY_PAGE_SIZE + 1


def test_discover_dedupes_boundary_item_across_pages():
    """GHL's cursor is inclusive — the last item of page N reappears as the first item of page N+1."""
    from app.worker.jobs import staff_call_quality_jobs as m

    full_page = [_conv(f"c{i}", 1000 + i) for i in range(m._DISCOVERY_PAGE_SIZE)]
    boundary_dup = full_page[-1]
    short_page = [boundary_dup, _conv("new", 99999)]
    mock_client = MagicMock()
    mock_client.search_conversations.side_effect = [full_page, short_page]

    result = _discover_conversations(mock_client)

    ids = [c["id"] for c in result]
    assert ids.count(boundary_dup["id"]) == 1
    assert len(result) == m._DISCOVERY_PAGE_SIZE + 1  # full_page + only the genuinely-new item


def test_discover_respects_max_page_safety_cap():
    """A location with continuous full pages must not loop forever."""
    from app.worker.jobs import staff_call_quality_jobs as m

    full_page = [_conv(f"c{i}", 1000 + i) for i in range(m._DISCOVERY_PAGE_SIZE)]
    mock_client = MagicMock()
    mock_client.search_conversations.return_value = full_page

    _discover_conversations(mock_client)

    assert mock_client.search_conversations.call_count == m._MAX_DISCOVERY_PAGES


def test_discover_stops_when_cursor_field_missing():
    """A malformed last item (no lastMessageDate) must stop pagination, not crash or loop with None."""
    mock_client = MagicMock()
    from app.worker.jobs import staff_call_quality_jobs as m

    full_page = [{"id": f"c{i}", "contactId": f"contact-{i}"} for i in range(m._DISCOVERY_PAGE_SIZE)]
    mock_client.search_conversations.return_value = full_page

    result = _discover_conversations(mock_client)

    assert mock_client.search_conversations.call_count == 1
    assert len(result) == m._DISCOVERY_PAGE_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# _parse_ghl_datetime
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_ghl_datetime_valid():
    result = _parse_ghl_datetime("2026-04-29T23:03:35.210Z")
    assert result.year == 2026 and result.month == 4 and result.day == 29


def test_parse_ghl_datetime_none_returns_none():
    assert _parse_ghl_datetime(None) is None


def test_parse_ghl_datetime_invalid_returns_none():
    assert _parse_ghl_datetime("not-a-date") is None


# ─────────────────────────────────────────────────────────────────────────────
# _filter_unprocessed
# ─────────────────────────────────────────────────────────────────────────────

def test_filter_unprocessed_excludes_existing_rows(session):
    existing = StaffCallQuality(
        id=str(uuid.uuid4()), ghl_message_id="msg-already-done",
        ghl_contact_id="c-1", call_time=datetime.now(tz=timezone.utc),
    )
    session.add(existing)
    session.flush()

    result = _filter_unprocessed(session, ["msg-already-done", "msg-new"])

    assert result == {"msg-new"}


def test_filter_unprocessed_empty_input():
    result = _filter_unprocessed(MagicMock(), [])
    assert result == set()


# ─────────────────────────────────────────────────────────────────────────────
# _has_enrolled_call_history
# ─────────────────────────────────────────────────────────────────────────────

def test_has_enrolled_call_history_true_via_direct_contact_id_match(session):
    session.add(CallEvent(
        id=str(uuid.uuid4()), call_id="call-1", contact_id="+15551234567",
        detected_intent="enrolled", dedupe_key=str(uuid.uuid4()),
    ))
    session.flush()
    assert _has_enrolled_call_history(session, "+15551234567") is True


def test_has_enrolled_call_history_true_via_lead_state_phone_match(session):
    session.add(LeadState(
        id=str(uuid.uuid4()), contact_id="ghl-contact-1", normalized_phone="+15559998888",
        version=0, created_at=datetime.now(tz=timezone.utc), updated_at=datetime.now(tz=timezone.utc),
    ))
    session.add(CallEvent(
        id=str(uuid.uuid4()), call_id="call-2", contact_id="ghl-contact-1",
        detected_intent="enrolled", dedupe_key=str(uuid.uuid4()),
    ))
    session.flush()
    assert _has_enrolled_call_history(session, "+15559998888") is True


def test_has_enrolled_call_history_false_when_no_match(session):
    assert _has_enrolled_call_history(session, "+15550000000") is False


def test_has_enrolled_call_history_false_for_non_enrolled_intent(session):
    session.add(CallEvent(
        id=str(uuid.uuid4()), call_id="call-3", contact_id="+15551112222",
        detected_intent="callback_request", dedupe_key=str(uuid.uuid4()),
    ))
    session.flush()
    assert _has_enrolled_call_history(session, "+15551112222") is False


# ─────────────────────────────────────────────────────────────────────────────
# _process_call_message — outcome gate
# ─────────────────────────────────────────────────────────────────────────────

def test_not_connected_call_skips_transcript_and_persists_minimal_row(session):
    settings = MagicMock()
    conv_client = MagicMock()
    contact_client = MagicMock()
    message = _connected_call_message(meta={"call": {"duration": None, "status": "busy"}})

    _process_call_message(
        session, conv_client, contact_client, settings,
        conv_id="conv-1", contact_id="ghl-c-1", phone="+15551234567", message=message,
    )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-1")).one()
    assert row.call_connected is False
    assert row.transcript_text is None
    conv_client.get_message_transcription.assert_not_called()
    conv_client.get_message_recording.assert_not_called()


def test_short_duration_call_not_treated_as_connected(session):
    settings = MagicMock()
    conv_client = MagicMock()
    contact_client = MagicMock()
    message = _connected_call_message(id="msg-short", meta={"call": {"duration": 5, "status": "completed"}})

    _process_call_message(
        session, conv_client, contact_client, settings,
        conv_id="conv-1", contact_id="ghl-c-1", phone="+15551234567", message=message,
    )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-short")).one()
    assert row.call_connected is False


# ─────────────────────────────────────────────────────────────────────────────
# _process_call_message — transcript source priority
# ─────────────────────────────────────────────────────────────────────────────

@patch("app.core.call_quality_scoring.score_call", return_value=None)
def test_uses_ghl_native_transcript_when_available(mock_score, session):
    settings = MagicMock()
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = [
        {"transcript": "Hello there."}, {"transcript": "How can I help?"},
    ]
    contact_client = MagicMock()
    contact_client.get_contact.return_value = {"customFields": []}
    message = _connected_call_message(id="msg-native")

    with patch("app.adapters.openai_client.OpenAIClient") as MockOAI:
        mock_oai_instance = MagicMock()
        MockOAI.return_value = mock_oai_instance

        _process_call_message(
            session, conv_client, contact_client, settings,
            conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
        )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-native")).one()
    assert row.transcript_source == "ghl_native"
    assert row.transcript_text == "Hello there. How can I help?"
    conv_client.get_message_recording.assert_not_called()
    mock_oai_instance.transcribe_audio.assert_not_called()


def test_falls_back_to_whisper_when_no_ghl_transcript(session):
    settings = MagicMock()
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = None
    conv_client.get_message_recording.return_value = b"fake-audio"
    contact_client = MagicMock()
    contact_client.get_contact.return_value = {"customFields": []}
    message = _connected_call_message(id="msg-whisper")

    with patch("app.adapters.openai_client.OpenAIClient") as MockOAI:
        mock_oai_instance = MagicMock()
        mock_oai_instance.transcribe_audio.return_value = "Whisper transcript text."
        MockOAI.return_value = mock_oai_instance

        with patch("app.core.call_quality_scoring.score_call", return_value=None):
            _process_call_message(
                session, conv_client, contact_client, settings,
                conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
            )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-whisper")).one()
    assert row.transcript_source == "openai_whisper"
    assert row.transcript_text == "Whisper transcript text."
    mock_oai_instance.transcribe_audio.assert_called_once()


def test_no_recording_available_sets_flagged_reason_no_score(session):
    settings = MagicMock()
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = None
    conv_client.get_message_recording.side_effect = GHLError("no recording", status_code=422)
    contact_client = MagicMock()
    message = _connected_call_message(id="msg-no-recording")

    _process_call_message(
        session, conv_client, contact_client, settings,
        conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
    )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-no-recording")).one()
    assert row.transcript_text is None
    assert row.quality_score is None
    assert "no transcript" in row.flagged_reason.lower()
    contact_client.get_contact.assert_not_called()  # never reached classification


# ─────────────────────────────────────────────────────────────────────────────
# _process_call_message — classification + scoring end to end
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline_persists_classification_and_score(session):
    settings = MagicMock()
    settings.openai_model_ghl_analysis = "gpt-4o-mini"
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = [{"transcript": "Let's talk pricing."}]
    contact_client = MagicMock()
    contact_client.get_contact.return_value = {
        "customFields": [{"name": "Who you are", "value": "Potential Student"}]
    }
    message = _connected_call_message(id="msg-full")

    with patch("app.adapters.openai_client.OpenAIClient") as MockOAI:
        mock_oai_instance = MagicMock()
        mock_oai_instance.chat_completion.return_value = {
            "overall_score": 88, "summary": "Solid call.", "flagged_reason": None,
        }
        MockOAI.return_value = mock_oai_instance

        _process_call_message(
            session, conv_client, contact_client, settings,
            conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
        )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-full")).one()
    assert row.conversation_type == "sales"
    assert row.conversation_type_source == "ghl_who_you_are"
    assert row.who_you_are_value == "Potential Student"
    assert row.quality_score == 88
    assert row.summary == "Solid call."


def test_ghl_tags_take_priority_over_picklist_fields(session):
    """Real data (2026-08-25): tags were populated when the picklist fields never were."""
    settings = MagicMock()
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = [{"transcript": "Checking in on your program interest."}]
    contact_client = MagicMock()
    contact_client.get_contact.return_value = {
        "tags": ["warm lead"],
        "customFields": [{"name": "Who you are", "value": "Current Student"}],
    }
    message = _connected_call_message(id="msg-tags")

    with patch("app.adapters.openai_client.OpenAIClient") as MockOAI:
        mock_oai_instance = MagicMock()
        mock_oai_instance.chat_completion.return_value = {"overall_score": 75, "summary": "Fine call."}
        MockOAI.return_value = mock_oai_instance

        _process_call_message(
            session, conv_client, contact_client, settings,
            conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
        )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-tags")).one()
    assert row.conversation_type == "sales"
    assert row.conversation_type_source == "ghl_tags"
    assert row.ghl_tags == ["warm lead"]
    # Picklist field snapshot still recorded even though tags decided it — audit trail intact.
    assert row.who_you_are_value == "Current Student"


def test_ai_fallback_classification_used_when_no_known_signals(session):
    settings = MagicMock()
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = [{"transcript": "Student billing question."}]
    contact_client = MagicMock()
    contact_client.get_contact.return_value = {"customFields": []}  # nothing populated
    message = _connected_call_message(id="msg-ai-fallback")

    with patch("app.adapters.openai_client.OpenAIClient") as MockOAI:
        mock_oai_instance = MagicMock()
        # First chat_completion call = AI classification; second = scoring
        mock_oai_instance.chat_completion.side_effect = [
            {"conversation_type": "support"},
            {"overall_score": 70, "summary": "Handled ok."},
        ]
        MockOAI.return_value = mock_oai_instance

        _process_call_message(
            session, conv_client, contact_client, settings,
            conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
        )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-ai-fallback")).one()
    assert row.conversation_type == "support"
    assert row.conversation_type_source == "ai_inferred"
    assert row.quality_score == 70


def test_other_conversation_type_not_scored(session):
    settings = MagicMock()
    conv_client = MagicMock()
    conv_client.get_message_transcription.return_value = [{"transcript": "Partnership inquiry."}]
    contact_client = MagicMock()
    contact_client.get_contact.return_value = {
        "customFields": [{"name": "Who you are", "value": "Business or Partner"}]
    }
    message = _connected_call_message(id="msg-other")

    with patch("app.adapters.openai_client.OpenAIClient") as MockOAI:
        mock_oai_instance = MagicMock()
        MockOAI.return_value = mock_oai_instance

        _process_call_message(
            session, conv_client, contact_client, settings,
            conv_id="conv-1", contact_id="ghl-c-1", phone=None, message=message,
        )

    row = session.scalars(select(StaffCallQuality).where(StaffCallQuality.ghl_message_id == "msg-other")).one()
    assert row.conversation_type == "other"
    assert row.quality_score is None
    mock_oai_instance.chat_completion.assert_not_called()
