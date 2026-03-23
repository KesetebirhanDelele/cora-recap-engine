"""
Rule-based intent detection for call transcripts.

No LLM is used. All detection is regex / keyword-based.

Priority order (strictly enforced — later checks must not shadow earlier ones):
  1. do_not_call
  2. wrong_number
  3. not_interested
  4. callback_with_time
  5. callback_request
  6. interested_not_now   ← warm but not ready; routes to nurture campaign
  7. call_later_no_time
  8. uncertain            ← on the fence; routes to shorter nurture window
  9. request_sms
  10. request_email

Returns None when no intent is detected — caller continues normal tier logic.

Output schema:
  {
    "intent":     str,
    "confidence": float,           # 0.0–1.0; rule-based so always 0.9 on match
    "entities": {
      "datetime": datetime | None, # extracted callback time for *_with_time intents
      "channel":  str | None,      # "sms" | "email" for channel-switch intents
    }
  }
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled pattern table — evaluated in PRIORITY ORDER
# ---------------------------------------------------------------------------

_RAW_PATTERNS: list[tuple[str, list[str]]] = [
    ("do_not_call", [
        r"\bdon'?t\s+call\b",
        r"\bdo\s+not\s+call\b",
        r"\bstop\s+calling\b",
        r"\bremove\s+(me|my\s+number)\b",
        r"\btake\s+(me|my\s+number)\s+off\b",
        r"\bunsubscribe\b",
        r"\bopt\s*[-\s]?out\b",
        r"\bnever\s+call\s+(me|again)\b",
    ]),
    ("wrong_number", [
        r"\bwrong\s+number\b",
        r"\bwrong\s+person\b",
        r"\byou\s+have\s+the\s+wrong\b",
        r"\bno\s+one\s+(here\s+)?by\s+that\s+name\b",
        r"\bi\s+don'?t\s+know\s+(a\s+)?[a-z]+\s+(from|at)\b",
    ]),
    ("not_interested", [
        r"\bnot\s+interested\b",
        r"\bno\s+thank\s+you\b",
        r"\bno\s+thanks\b",
        r"\bnot\s+looking\b",
        r"\balready\s+(enrolled|signed\s+up|taken\s+care\s+of)\b",
        r"\bdon'?t\s+need\s+(this|it|your)\b",
        r"\bnot\s+for\s+me\b",
        r"\bi'?m\s+not\s+interested\b",
        r"\bpass\b",
    ]),
    ("callback_with_time", [
        # "call me back tomorrow", "call me next monday at 3pm", etc.
        r"\bcall\s+(me\s+)?(back\s+)?(tomorrow|tonight|this\s+(morning|afternoon|evening))\b",
        r"\bcall\s+(me\s+)?(back\s+)?next\s+(week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\bcall\s+(me\s+)?(back\s+)?(at|around)\s+\d{1,2}\s*(am|pm)\b",
        r"\bin\s+\d+\s+(hour|day|week)s?\b",
        r"\b(tomorrow|next\s+week|next\s+month)\s+at\s+\d{1,2}\b",
        r"\b\d{1,2}\s*(am|pm)\b",
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d\b",
    ]),
    ("callback_request", [
        r"\bcall\s+me\s+back\b",
        r"\bgive\s+me\s+a\s+call\b",
        r"\bring\s+me\s+back\b",
        r"\bplease\s+call\b",
        r"\bcan\s+you\s+call\b",
        r"\bi'?ll\s+answer\b",
        r"\bcall\s+me\s+when\b",
    ]),
    ("interested_not_now", [
        r"\bnot\s+right\s+now\b",
        r"\bmaybe\s+later\b",
        r"\bsome\s+other\s+time\b",
        r"\bsometime\s+(next|later)\b",
        r"\bcall\s+me\s+sometime\b",
        r"\bi'?m\s+(busy|tied\s+up)\b",
        r"\bfollow\s+up\s+(later|with\s+me)\b",
        r"\bcheck\s+back\s+(later|with\s+me)\b",
        r"\bsend\s+(me\s+)?(some\s+)?(something|information|info|details|your\s+info)\b",
        r"\bnot\s+a\s+good\s+time\b",
        r"\bi'?m\s+busy\s+(these|right)\b",
        r"\bnot\s+now\b",
    ]),
    ("call_later_no_time", [
        r"\bcall\s+(me\s+)?later\b",
        r"\btry\s+(me\s+)?(again\s+)?later\b",
        r"\bcall\s+back\s+later\b",
        r"\btry\s+again\b",
        r"\banother\s+time\b",
        r"\bcall\s+me\s+again\b",
    ]),
    ("uncertain", [
        # Softer signals — lead is on the fence; routes to shorter nurture window.
        # Evaluated AFTER all scheduling and interested_not_now checks so that
        # "maybe later" and "call me sometime" are already caught above.
        r"\bnot\s+sure\b",
        r"\bi'?m\s+unsure\b",
        r"\bi'?l{1,2}\s+think\s+about\s+(it|this)\b",
        r"\blet\s+me\s+(think|consider)\b",
        r"\bneed\s+(some\s+)?time\s+to\s+(think|decide)\b",
        r"\bundecided\b",
        r"\bmaybe\b",
    ]),
    ("request_sms", [
        r"\btext\s+me\b",
        r"\bsend\s+me\s+a\s+text\b",
        r"\bvia\s+text\b",
        r"\bby\s+text\b",
        r"\bsms\b",
        r"\bwhatsapp\b",
    ]),
    ("request_email", [
        r"\bemail\s+me\b",
        r"\bsend\s+(me\s+)?an?\s+email\b",
        r"\bvia\s+email\b",
        r"\bby\s+email\b",
        r"\be[-\s]?mail\s+me\b",
    ]),
]

# Compiled once at module load
_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (intent, [re.compile(p, re.IGNORECASE) for p in patterns])
    for intent, patterns in _RAW_PATTERNS
]

# ---------------------------------------------------------------------------
# Datetime extractor (used for callback_with_time)
# ---------------------------------------------------------------------------

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _extract_callback_datetime(text: str) -> datetime | None:
    """
    Parse a callback time expression from normalized text.

    Returns a UTC-aware datetime, or None if nothing specific was found.
    The caller falls back to a default delay when None is returned.
    """
    now = datetime.now(tz=timezone.utc)

    # "in N hours"
    m = re.search(r"\bin\s+(\d+)\s+hours?\b", text, re.IGNORECASE)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    # "in N days"
    m = re.search(r"\bin\s+(\d+)\s+days?\b", text, re.IGNORECASE)
    if m:
        return now + timedelta(days=int(m.group(1)))

    # "in N weeks"
    m = re.search(r"\bin\s+(\d+)\s+weeks?\b", text, re.IGNORECASE)
    if m:
        return now + timedelta(weeks=int(m.group(1)))

    # "tomorrow at Xpm" or "tomorrow at X:YYpm"
    m = re.search(r"\btomorrow\b.*?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.IGNORECASE)
    if m:
        return _build_future_dt(int(m.group(1)), m.group(3), offset_days=1,
                                minute=int(m.group(2) or 0))

    # plain "tomorrow"
    if re.search(r"\btomorrow\b", text, re.IGNORECASE):
        return now + timedelta(days=1)

    # "tonight" / "this evening"
    if re.search(r"\btonight\b|\bthis\s+evening\b", text, re.IGNORECASE):
        return _build_future_dt(7, "pm", offset_days=0)  # default 7 PM today

    # "this morning" / "this afternoon"
    m = re.search(r"\bthis\s+(morning|afternoon)\b", text, re.IGNORECASE)
    if m:
        hour = 9 if m.group(1) == "morning" else 14
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # "next week"
    if re.search(r"\bnext\s+week\b", text, re.IGNORECASE):
        return now + timedelta(weeks=1)

    # "next month"
    if re.search(r"\bnext\s+month\b", text, re.IGNORECASE):
        return now + timedelta(days=30)

    # "at Xpm" or standalone "Xpm"
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
                  r"|\b(\d{1,2})\s*(am|pm)\b", text, re.IGNORECASE)
    if m:
        hour_s = m.group(1) or m.group(4)
        ampm = m.group(3) or m.group(5)
        minute = int(m.group(2) or 0)
        return _build_future_dt(int(hour_s), ampm, offset_days=0, minute=minute)

    # Day of week
    for day_name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{day_name}\b", text, re.IGNORECASE):
            days_ahead = (weekday - now.weekday()) % 7 or 7
            return now + timedelta(days=days_ahead)

    return None


def _build_future_dt(
    hour_12: int, ampm: str, offset_days: int, minute: int = 0
) -> datetime:
    """Convert 12-hour clock + am/pm to a UTC-aware datetime."""
    now = datetime.now(tz=timezone.utc)
    hour = hour_12
    ampm = ampm.lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    base = now + timedelta(days=offset_days)
    result = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # If the resulting time is in the past, shift one day forward
    if result <= now:
        result += timedelta(days=1)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_intent(transcript: str) -> dict[str, Any] | None:
    """
    Detect the primary intent from a call transcript.

    Returns a result dict on match, None when no intent is found.

    Result schema:
      {
        "intent":     str,
        "confidence": float,
        "entities": {
          "datetime": datetime | None,
          "channel":  str | None,
        }
      }
    """
    if not transcript or not transcript.strip():
        return None

    normalized = transcript.strip().lower()

    for intent, patterns in _COMPILED:
        for pattern in patterns:
            if pattern.search(normalized):
                entities: dict[str, Any] = {"datetime": None, "channel": None}

                if intent == "callback_with_time":
                    entities["datetime"] = _extract_callback_datetime(normalized)

                elif intent == "request_sms":
                    entities["channel"] = "sms"

                elif intent == "request_email":
                    entities["channel"] = "email"

                result = {
                    "intent": intent,
                    "confidence": 0.9,
                    "entities": entities,
                }
                logger.debug(
                    "detect_intent: matched | intent=%s pattern=%r",
                    intent, pattern.pattern,
                )
                return result

    return None
