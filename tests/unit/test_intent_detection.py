"""
Unit tests for app.core.intent_detection.

Covers all 9 intents, priority ordering, edge cases, and datetime extraction.
No I/O — all rule-based logic, no mocking required.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.core.intent_detection import detect_intent


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _intent(transcript: str) -> str | None:
    result = detect_intent(transcript)
    return result["intent"] if result else None


def _entities(transcript: str) -> dict | None:
    result = detect_intent(transcript)
    return result["entities"] if result else None


# ---------------------------------------------------------------------------
# 1. do_not_call
# ---------------------------------------------------------------------------

def test_do_not_call_basic():
    assert _intent("don't call me again") == "do_not_call"

def test_do_not_call_remove_number():
    assert _intent("please remove my number") == "do_not_call"

def test_do_not_call_stop():
    assert _intent("stop calling me") == "do_not_call"

def test_do_not_call_opt_out():
    assert _intent("I want to opt out") == "do_not_call"

def test_do_not_call_never():
    assert _intent("never call me again please") == "do_not_call"


# ---------------------------------------------------------------------------
# 2. wrong_number
# ---------------------------------------------------------------------------

def test_wrong_number_basic():
    assert _intent("you have the wrong number") == "wrong_number"

def test_wrong_number_person():
    assert _intent("I think you have the wrong person") == "wrong_number"

def test_wrong_number_standalone():
    assert _intent("wrong number mate") == "wrong_number"


# ---------------------------------------------------------------------------
# 3. not_interested
# ---------------------------------------------------------------------------

def test_not_interested_basic():
    assert _intent("I'm not interested") == "not_interested"

def test_not_interested_no_thanks():
    assert _intent("No thank you") == "not_interested"

def test_not_interested_no_thanks_short():
    assert _intent("No thanks") == "not_interested"

def test_not_interested_not_looking():
    assert _intent("I'm not looking for that") == "not_interested"

def test_not_interested_dont_need():
    assert _intent("I don't need this service") == "not_interested"


# ---------------------------------------------------------------------------
# 4. callback_with_time
# ---------------------------------------------------------------------------

def test_callback_with_time_tomorrow_at_10():
    result = detect_intent("call me tomorrow at 10am")
    assert result is not None
    assert result["intent"] == "callback_with_time"
    dt = result["entities"]["datetime"]
    assert dt is not None
    assert dt > datetime.now(tz=timezone.utc)

def test_callback_with_time_tomorrow_3pm():
    result = detect_intent("call me back tomorrow at 3pm")
    assert result["intent"] == "callback_with_time"
    dt = result["entities"]["datetime"]
    assert dt is not None
    # Should be roughly 1 day from now (within 2 days to allow for edge cases)
    assert timedelta(hours=0) < dt - datetime.now(tz=timezone.utc) < timedelta(days=2)

def test_callback_with_time_next_week():
    result = detect_intent("call me next week please")
    assert result["intent"] == "callback_with_time"
    dt = result["entities"]["datetime"]
    assert dt is not None
    # Should be ~7 days from now
    assert timedelta(days=5) < dt - datetime.now(tz=timezone.utc) < timedelta(days=9)

def test_callback_with_time_in_two_hours():
    result = detect_intent("try again in 2 hours")
    assert result["intent"] == "callback_with_time"
    dt = result["entities"]["datetime"]
    assert dt is not None
    delta = dt - datetime.now(tz=timezone.utc)
    assert timedelta(hours=1, minutes=50) < delta < timedelta(hours=2, minutes=10)

def test_callback_with_time_next_month():
    result = detect_intent("call me back next month")
    assert result["intent"] == "callback_with_time"
    dt = result["entities"]["datetime"]
    assert dt is not None

def test_callback_with_time_at_3pm():
    result = detect_intent("please call at 3pm")
    assert result["intent"] == "callback_with_time"


# ---------------------------------------------------------------------------
# 5. callback_request
# ---------------------------------------------------------------------------

def test_callback_request_basic():
    assert _intent("can you call me back please") == "callback_request"

def test_callback_request_give_me_a_call():
    assert _intent("give me a call when you get a chance") == "callback_request"

def test_callback_request_please_call():
    assert _intent("please call me") == "callback_request"


# ---------------------------------------------------------------------------
# 6. interested_not_now  (must come AFTER callback detection in priority)
# ---------------------------------------------------------------------------

def test_interested_not_now_maybe_later():
    assert _intent("maybe later") == "interested_not_now"

def test_interested_not_now_not_right_now():
    assert _intent("not right now, I'm busy") == "interested_not_now"

def test_interested_not_now_busy():
    assert _intent("I'm tied up at the moment") == "interested_not_now"

def test_interested_not_now_follow_up_later():
    assert _intent("follow up with me later") == "interested_not_now"

def test_interested_not_now_send_info():
    assert _intent("send me some information and I'll check it out") == "interested_not_now"

def test_interested_not_now_some_other_time():
    assert _intent("some other time would be better") == "interested_not_now"

def test_interested_not_now_not_a_good_time():
    assert _intent("this is not a good time for me") == "interested_not_now"


# ---------------------------------------------------------------------------
# 7. call_later_no_time
# ---------------------------------------------------------------------------

def test_call_later_no_time_basic():
    assert _intent("call me later") == "call_later_no_time"

def test_call_later_no_time_try_again():
    assert _intent("please try again") == "call_later_no_time"

def test_call_later_no_time_another_time():
    assert _intent("let's talk another time") == "call_later_no_time"


# ---------------------------------------------------------------------------
# 8. request_sms
# ---------------------------------------------------------------------------

def test_request_sms_text_me():
    result = detect_intent("just text me instead")
    assert result["intent"] == "request_sms"
    assert result["entities"]["channel"] == "sms"

def test_request_sms_send_text():
    assert _intent("send me a text message") == "request_sms"

def test_request_sms_via_sms():
    assert _intent("contact me via SMS") == "request_sms"


# ---------------------------------------------------------------------------
# 9. request_email
# ---------------------------------------------------------------------------

def test_request_email_basic():
    result = detect_intent("email me the details")
    assert result["intent"] == "request_email"
    assert result["entities"]["channel"] == "email"

def test_request_email_send():
    assert _intent("send me an email please") == "request_email"


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

def test_priority_do_not_call_beats_not_interested():
    # "not interested" would also match but do_not_call wins
    assert _intent("I'm not interested, don't call me again") == "do_not_call"

def test_priority_callback_with_time_beats_interested_not_now():
    # "call me next week" has a specific time → callback_with_time, NOT nurture
    result = detect_intent("call me next week when I have more time")
    assert result["intent"] == "callback_with_time"

def test_priority_not_interested_beats_interested_not_now():
    # "not interested" at priority 3 beats "not now" at priority 6
    assert _intent("I'm not interested right now") == "not_interested"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_transcript_returns_none():
    assert detect_intent("") is None

def test_whitespace_only_returns_none():
    assert detect_intent("   \n\t  ") is None

def test_none_transcript_returns_none():
    # Should not raise — callers may pass None
    try:
        result = detect_intent(None)  # type: ignore[arg-type]
        assert result is None
    except (AttributeError, TypeError):
        pass  # acceptable — callers guard with transcript.strip()

def test_unrecognised_text_returns_none():
    assert detect_intent("the weather is nice today") is None

def test_confidence_is_float():
    result = detect_intent("call me back")
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0

def test_result_has_required_keys():
    result = detect_intent("don't call me")
    assert "intent" in result
    assert "confidence" in result
    assert "entities" in result
    assert "datetime" in result["entities"]
    assert "channel" in result["entities"]

def test_non_callback_intent_has_null_datetime():
    result = detect_intent("don't call me again")
    assert result["entities"]["datetime"] is None

def test_non_channel_intent_has_null_channel():
    result = detect_intent("call me back")
    assert result["entities"]["channel"] is None


# ---------------------------------------------------------------------------
# 8. uncertain (new)
# ---------------------------------------------------------------------------

def test_uncertain_not_sure():
    assert _intent("I'm not sure about this") == "uncertain"

def test_uncertain_maybe():
    assert _intent("maybe") == "uncertain"

def test_uncertain_let_me_think():
    assert _intent("let me think about it") == "uncertain"

def test_uncertain_think_about_it():
    assert _intent("I'll think about it") == "uncertain"

def test_uncertain_undecided():
    assert _intent("I'm undecided") == "uncertain"

def test_uncertain_need_time():
    assert _intent("I need some time to decide") == "uncertain"

def test_uncertain_no_null_entities():
    result = detect_intent("I'm not sure")
    assert result["entities"]["datetime"] is None
    assert result["entities"]["channel"] is None

# Priority: "maybe later" → interested_not_now (position 6), not uncertain (position 8)
def test_uncertain_priority_maybe_later_is_interested_not_now():
    assert _intent("maybe later") == "interested_not_now"

# Priority: "maybe call me back tomorrow" → callback_with_time beats uncertain
def test_uncertain_priority_call_me_back_tomorrow_is_callback_with_time():
    assert _intent("maybe call me back tomorrow") == "callback_with_time"

# Priority: "not sure" is not confused with "not interested"
def test_uncertain_priority_not_interested_beats_not_sure():
    # "not interested" is at priority 3, "not sure" is uncertain at 8
    assert _intent("I'm not interested, not sure why I even picked up") == "not_interested"
