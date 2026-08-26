"""
Unit tests for _fetch_ghl_messages() and the ghl_messages field on ConversationContext.

Covers:
  1. ghl_messages defaults to empty list when fetch is disabled
  2. ghl_messages populated when ghl_fetch_conversation_history = True
  3. Returns empty list when no call_event found for contact
  4. Returns empty list when call_event has no phone in payload
  5. Returns empty list when GHL contact not found (phone lookup misses)
  6. Returns empty list when no conversations exist for contact
  7. Returns empty list when conversation has no messages
  8. Activity messages are filtered out; SMS/Email messages are kept
  9. Messages are normalised to {direction, type, body, date}
  10. GHLError during fetch is caught and returns empty list (non-fatal)
  11. phone_number_to preferred regardless of direction (falls back to phone_number_from)
  12. _format_ghl_thread: empty list returns placeholder string
  13. _format_ghl_thread: messages formatted with LEAD/US tags, newest-last
  14. _format_ghl_thread: body truncated at 200 chars, date trimmed to YYYY-MM-DD
  15. _format_ghl_thread: caps at 8 messages
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.conversation_context import ConversationContext, _fetch_ghl_messages
from app.core.ai_message_generator import _format_ghl_thread


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_call_event(direction: str = "Outbound", phone_to: str = "+15550001111", phone_from: str = "+18001234567"):
    evt = MagicMock()
    evt.direction = direction
    evt.raw_payload_json = {
        "phone_number_to": phone_to,
        "phone_number_from": phone_from,
    }
    return evt


def _mock_session(call_event=None):
    """Build a minimal SQLAlchemy session mock that returns `call_event` from scalars."""
    session = MagicMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = call_event
    session.scalars.return_value = scalars_result
    return session


# ─────────────────────────────────────────────────────────────────────────────
# 1. ghl_messages defaults to empty list
# ─────────────────────────────────────────────────────────────────────────────

def test_ghl_messages_default_empty():
    ctx = ConversationContext(
        contact_id="c1", campaign_name=None, status=None, tier=None, preferred_channel=None
    )
    assert ctx.ghl_messages == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. fetch disabled → ghl_messages stays empty inside get_conversation_context
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_skipped_when_setting_false():
    """When ghl_fetch_conversation_history=False, _fetch_ghl_messages is never called."""
    with patch("app.config.get_settings") as mock_settings, \
         patch("app.core.conversation_context._fetch_ghl_messages") as mock_fetch:
        mock_settings.return_value = MagicMock(ghl_fetch_conversation_history=False)
        mock_fetch.return_value = []

        from app.core.conversation_context import get_conversation_context

        session = MagicMock()
        session.scalars.return_value.first.return_value = None
        session.scalars.return_value.all.return_value = []

        ctx = get_conversation_context(session, "cid-1")
        mock_fetch.assert_not_called()
        assert ctx.ghl_messages == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Returns empty list when no call_event exists
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_no_call_event_returns_empty():
    session = _mock_session(call_event=None)
    result = _fetch_ghl_messages(session, "cid-1")
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Returns empty list when call_event has no phone in payload
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_no_phone_in_payload_returns_empty():
    evt = MagicMock()
    evt.direction = "Outbound"
    evt.raw_payload_json = {}  # no phone fields
    session = _mock_session(call_event=evt)
    result = _fetch_ghl_messages(session, "cid-1")
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. Returns empty list when GHL contact not found
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_contact_not_found_returns_empty():
    session = _mock_session(call_event=_mock_call_event())
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = None
        result = _fetch_ghl_messages(session, "cid-1")
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. Returns empty list when no conversations exist
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_no_conversations_returns_empty():
    session = _mock_session(call_event=_mock_call_event())
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = {"id": "ghl-cid"}
        instance.get_conversations_by_contact.return_value = []
        result = _fetch_ghl_messages(session, "cid-1")
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 7. Returns empty list when conversation has no messages
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_empty_messages_returns_empty():
    session = _mock_session(call_event=_mock_call_event())
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = {"id": "ghl-cid"}
        instance.get_conversations_by_contact.return_value = [{"id": "conv-1"}]
        instance.get_conversation_messages.return_value = []
        result = _fetch_ghl_messages(session, "cid-1")
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 8. Activity messages filtered out; SMS messages kept
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_filters_activity_messages():
    session = _mock_session(call_event=_mock_call_event())
    raw = [
        {"messageType": "Activity", "direction": "inbound", "body": "Status changed", "dateAdded": "2025-07-01"},
        {"messageType": "SMS", "direction": "inbound", "body": "Hello!", "dateAdded": "2025-07-01"},
        {"messageType": "Call", "direction": "outbound", "body": "Call log", "dateAdded": "2025-07-01"},
    ]
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = {"id": "ghl-cid"}
        instance.get_conversations_by_contact.return_value = [{"id": "conv-1"}]
        instance.get_conversation_messages.return_value = raw
        result = _fetch_ghl_messages(session, "cid-1")

    assert len(result) == 1
    assert result[0]["body"] == "Hello!"
    assert result[0]["type"] == "SMS"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Messages normalised to {direction, type, body, date}
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_normalises_message_shape():
    session = _mock_session(call_event=_mock_call_event())
    raw = [{"messageType": "Email", "direction": "outbound", "body": "Hi there", "dateAdded": "2025-07-10T15:00:00Z"}]
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = {"id": "ghl-cid"}
        instance.get_conversations_by_contact.return_value = [{"id": "conv-1"}]
        instance.get_conversation_messages.return_value = raw
        result = _fetch_ghl_messages(session, "cid-1")

    assert result == [{"direction": "outbound", "type": "Email", "body": "Hi there", "date": "2025-07-10T15:00:00Z"}]


# ─────────────────────────────────────────────────────────────────────────────
# 10. GHLError during fetch is swallowed — returns empty list
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_ghl_error_returns_empty():
    from app.adapters.ghl import GHLError
    session = _mock_session(call_event=_mock_call_event())
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.side_effect = GHLError("timeout")
        result = _fetch_ghl_messages(session, "cid-1")
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 11. Both inbound and outbound calls prefer phone_number_to (spec/24:
#     phone_number_from is Synthflow's own agent line, not the lead's number,
#     regardless of call direction — verified against prod call_events).
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_inbound_prefers_phone_to_over_agent_line():
    """
    Regression (spec/24): phone_number_from on an inbound call is Synthflow's
    own agent line (e.g. the number that answered), not the caller's number.
    phone_number_to must be used instead, or the GHL contact lookup resolves
    to the wrong (or no) contact.
    """
    evt = _mock_call_event(direction="inbound", phone_from="+15559990000", phone_to="+18001234567")
    session = _mock_session(call_event=evt)
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = None  # just checking which phone is passed
        _fetch_ghl_messages(session, "cid-1")
        called_phone = instance.search_contact_by_phone.call_args[0][0]
    assert called_phone == "+18001234567"


def test_fetch_outbound_uses_phone_to():
    evt = _mock_call_event(direction="Outbound", phone_to="+15551112222", phone_from="+18001234567")
    session = _mock_session(call_event=evt)
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = None
        _fetch_ghl_messages(session, "cid-1")
        called_phone = instance.search_contact_by_phone.call_args[0][0]
    assert called_phone == "+15551112222"


def test_fetch_falls_back_to_phone_from_when_phone_to_absent():
    """phone_number_from is still used as a last resort when phone_number_to is missing."""
    evt = _mock_call_event(direction="inbound", phone_from="+15559990000", phone_to="")
    evt.raw_payload_json = {"phone_number_from": "+15559990000"}
    session = _mock_session(call_event=evt)
    with patch("app.adapters.ghl.GHLClient") as MockClient:
        instance = MockClient.return_value
        instance.search_contact_by_phone.return_value = None
        _fetch_ghl_messages(session, "cid-1")
        called_phone = instance.search_contact_by_phone.call_args[0][0]
    assert called_phone == "+15559990000"


# ─────────────────────────────────────────────────────────────────────────────
# 12. _format_ghl_thread: empty list → placeholder
# ─────────────────────────────────────────────────────────────────────────────

def test_format_ghl_thread_empty():
    result = _format_ghl_thread([])
    assert result == "(no GHL conversation history)"


# ─────────────────────────────────────────────────────────────────────────────
# 13. _format_ghl_thread: LEAD/US tags, chronological order
# ─────────────────────────────────────────────────────────────────────────────

def test_format_ghl_thread_tags_and_order():
    messages = [
        {"direction": "outbound", "type": "SMS", "body": "Hi from us", "date": "2025-07-01"},
        {"direction": "inbound", "type": "SMS", "body": "Hi back", "date": "2025-07-02"},
    ]
    # ghl_messages arrive newest-first → reversed inside formatter
    result = _format_ghl_thread(list(reversed(messages)))
    lines = result.split("\n")
    assert "[US SMS 2025-07-01] Hi from us" in lines[0]
    assert "[LEAD SMS 2025-07-02] Hi back" in lines[1]


# ─────────────────────────────────────────────────────────────────────────────
# 14. _format_ghl_thread: body truncated at 200 chars, date at 10 chars
# ─────────────────────────────────────────────────────────────────────────────

def test_format_ghl_thread_truncation():
    long_body = "x" * 300
    messages = [{"direction": "inbound", "type": "SMS", "body": long_body, "date": "2025-07-10T15:00:00Z"}]
    result = _format_ghl_thread(messages)
    # body capped at 200
    assert "x" * 200 in result
    assert "x" * 201 not in result
    # date trimmed to YYYY-MM-DD (10 chars)
    assert "2025-07-10" in result
    assert "T15:00:00Z" not in result


# ─────────────────────────────────────────────────────────────────────────────
# 15. _format_ghl_thread: caps at 8 messages (takes last 8)
# ─────────────────────────────────────────────────────────────────────────────

def test_format_ghl_thread_max_messages():
    messages = [
        {"direction": "inbound", "type": "SMS", "body": f"msg {i}", "date": "2025-07-01"}
        for i in range(12)
    ]
    result = _format_ghl_thread(messages)
    lines = [l for l in result.split("\n") if l.strip()]
    assert len(lines) == 8
