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

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Executed-actions parser (used to reinforce intent detection)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Carrier / phone-system recordings (spec/41)
# ---------------------------------------------------------------------------
# On a dead or blocked number the "human" in the transcript is a recording ("We could not complete your call. Please try again.",
# "This number is not in service. Please try again later."). The words "try again" used to match call_later_no_time, which scheduled
# a callback every 2 hours, exempt from the daily cap and with no retry count: one number was dialled 15 times in 3 days.
CARRIER_PHRASES: tuple[str, ...] = (
    "not in service", "could not complete your call", "call could not be completed", "call cannot be completed",
    "call can't be completed", "has been disconnected", "no longer in service", "not a working number",
    "not a valid number", "number you have dialed", "number you have called", "number you dialed", "number you called",
    "check the number and try again",
)
_CARRIER_MAX_HUMAN_CHARS = 220          # a recording is short; a real conversation is not


def is_carrier_message(transcript: str | None) -> bool:
    """True when what the 'human' said is a carrier / phone-system recording and nothing else (no real conversation)."""
    lines = [ln.split(":", 1)[1].strip().lower() for ln in (transcript or "").splitlines() if ln.strip().lower().startswith("human:")]
    human = " ".join(lines)
    if not human or len(human) > _CARRIER_MAX_HUMAN_CHARS:
        return False
    return any(ph in human for ph in CARRIER_PHRASES)


# ---------------------------------------------------------------------------
# What the PERSON said, and answering-machine greetings (spec/42)
# ---------------------------------------------------------------------------
# Callback patterns used to run over the whole transcript, including the bot's own lines and voicemail greetings ("leave a message and
# we will call you back"), so a machine or the bot could "ask" for a callback. They now read only the lines the person spoke.
_CALLBACK_FAMILY = frozenset({"callback_with_time", "callback_request", "call_later_no_time"})
_VOICEMAIL_GREETING = re.compile(
    r"leave\s+(a|your)\s+(message|name)|after\s+the\s+(tone|beep)|mailbox|voice\s*mail|can'?t\s+come\s+to\s+the\s+phone|"
    r"cannot\s+come\s+to\s+the\s+phone|record\s+your\s+(message|name)|messaging\s+service|not\s+available\s+(right\s+now|to\s+take)|"
    r"unavailable\s+to\s+take\s+your\s+call|(press|dial)\s+\d", re.IGNORECASE)
_VOICEMAIL_MAX_HUMAN_CHARS = 300


def human_text(transcript: str | None) -> str | None:
    """Only the lines the person spoke, or None when the transcript has no speaker labels (plain text: keep matching everything)."""
    lines = [ln.split(":", 1)[1].strip() for ln in (transcript or "").splitlines() if ln.strip().lower().startswith("human:")]
    has_labels = any(ln.strip().lower().startswith(("human:", "bot:")) for ln in (transcript or "").splitlines())
    return " ".join(lines) if has_labels else None


def is_voicemail_greeting(text: str | None) -> bool:
    """True when what the 'person' said is an answering-machine greeting (short, and a voicemail phrase)."""
    t = (text or "").strip()
    return bool(t) and len(t) <= _VOICEMAIL_MAX_HUMAN_CHARS and bool(_VOICEMAIL_GREETING.search(t))


_TRANSFER_FAIL_WORDS = ("fail", "error", "timeout", "timed-out", "blocked", "busy", "no-answer", "unavailable", "rejected")


def _transfer_status_failed(status: str, failed_reason: str) -> bool:
    """True when a Synthflow transfer action did not connect. Real values seen in production (2026-10-23):
    status "transfer-success" (failed_reason ""), and status "transfer-failed-connection-error" with failed_reason
    "transfer_blocked_by_carrier" or "transfer_outside_availability". A non-empty failed_reason alone also counts."""
    st = (status or "").lower()
    if (failed_reason or "").strip():
        return True
    return any(w in st for w in _TRANSFER_FAIL_WORDS)


def _extract_executed_actions(executed_actions: list | dict | None) -> dict:
    """
    Parse Synthflow executed_actions into structured signal flags.

    Synthflow sends executed_actions as a dict keyed by action name
    (e.g. {"action_ghl_create_booking": {...}, "rag_action": {...}}).
    Each action dict uses "action_type" (not "type"/"action") and stores
    outcome in a JSON string under "return_value" (not a top-level "status").

    Returns:
      {
        "transfer_attempted": bool,
        "transfer_failed":    bool,   # a transfer action failed (see _transfer_status_failed)
        "transfer_succeeded": bool,   # a transfer action connected (a later retry can succeed after a failure)
        "transfer_target":    str,    # action name of the last failed transfer, e.g. "admissions_transfer_roselen"
        "transfer_reason":    str,    # its failed_reason, e.g. "transfer_blocked_by_carrier"
        "booking_success":    bool,
        "booking_failed":     bool,
      }
    """
    flags = {
        "transfer_attempted": False,
        "transfer_failed":    False,
        "transfer_succeeded": False,
        "transfer_target":    "",
        "transfer_reason":    "",
        "booking_success":    False,
        "booking_failed":     False,
    }
    if not executed_actions:
        return flags

    # Normalize: Synthflow sends a dict of {name: action_dict}; accept lists too.
    if isinstance(executed_actions, dict):
        action_items: list = list(executed_actions.values())
    elif isinstance(executed_actions, list):
        action_items = executed_actions
    else:
        return flags

    for action in action_items:
        if not isinstance(action, dict):
            continue

        action_type = (
            action.get("action_type") or action.get("type") or action.get("action") or ""
        ).lower()

        # Status may be a top-level field or embedded in a JSON return_value string.
        status = ""
        failed_reason = ""
        raw_return = action.get("return_value")
        if isinstance(raw_return, str):
            try:
                rv = json.loads(raw_return)
                status = (rv.get("status") or "").lower()
                failed_reason = str(rv.get("failed_reason") or "")
            except (json.JSONDecodeError, AttributeError, ValueError):
                pass
        elif isinstance(raw_return, dict):
            status = (raw_return.get("status") or "").lower()
            failed_reason = str(raw_return.get("failed_reason") or "")
        if not status:
            status = (action.get("status") or action.get("result") or "").lower()

        if any(kw in action_type for kw in ("transfer", "handoff", "live_agent")):
            flags["transfer_attempted"] = True
            if _transfer_status_failed(status, failed_reason):
                flags["transfer_failed"] = True
                flags["transfer_target"] = str(action.get("name") or action_type)
                flags["transfer_reason"] = failed_reason or status
            else:
                flags["transfer_succeeded"] = True

        if any(kw in action_type for kw in ("booking", "schedule", "appointment", "calendar")):
            if status in ("success", "completed", "confirmed"):
                flags["booking_success"] = True
            elif status in ("failed", "error", "cancelled"):
                flags["booking_failed"] = True

    return flags


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
    ("enrolled", [
        # Lead confirms enrollment or registration in the program — campaign termination
        r"\bi'?d\s+like\s+to\s+enroll\b",
        r"\bi\s+want\s+to\s+(enroll|register)\b",
        r"\byes\s+i'?l{1,2}\s+enroll\b",
        r"\bi'?m\s+ready\s+to\s+enroll\b",
        r"\bi\s+agree\s+to\s+enroll\b",
        r"\bi\s+(registered|enrolled|signed\s+up)\b",
        r"\bi'?ve\s+(registered|enrolled|signed\s+up)\b",
        r"\bi\s+want\s+to\s+join\s+the\s+program\b",
        r"\benrollment\s+confirmed\b",
        r"\bregistration\s+confirmed\b",
        r"\bi\s+completed\s+the\s+enrollment\b",
        r"\bsign\s+me\s+up\b",
    ]),
    ("human_transfer_request", [
        r"\btalk\s+to\s+(a\s+)?(real\s+)?(someone|person|human|agent|representative|rep)\b",
        r"\bspeak\s+(with|to)\s+(a\s+)?(real\s+)?(person|human|agent|representative)\b",
        r"\breal\s+person\b",
        r"\bhuman\s+agent\b",
        r"\btransfer\s+me\b",
        r"\btransfer\s+to\s+(a\s+)?(human|person|agent|representative)\b",
        r"\bconnect\s+me\s+(with|to)\s+(a\s+)?(human|person|agent|representative)\b",
        r"\bcan\s+i\s+(talk|speak)\s+to\s+(a\s+)?(human|person|agent|representative)\b",
    ]),
    ("re_engaged", [
        # Cold lead expressing genuine interest — triggers campaign switch to New Lead
        r"\bactually\s+i'?m?\s+(am\s+)?interested\b",
        r"\bi'?m\s+interested\s+now\b",
        r"\bactually\s+yes\b",
        r"\btell\s+me\s+more\b",
        r"\bi'?d\s+like\s+to\s+(hear|learn|know)\s+more\b",
        r"\bi\s+want\s+to\s+(learn|know)\s+more\b",
        r"\bhow\s+does\s+it\s+work\b",
        r"\bwhat\s+are\s+the\s+details\b",
        r"\bi'?ve\s+been\s+thinking\s+about\s+it\b",
        r"\bi'?m\s+ready\s+to\s+talk\b",
        r"\byes\s+i'?m?\s+(am\s+)?interested\b",
        r"\byes\s+please\s+tell\s+me\b",
    ]),
    ("callback_with_time", [
        # "call me back tomorrow", "call me next monday at 3pm", etc.
        r"\bcall\s+(me\s+)?(back\s+)?(tomorrow|tonight|this\s+(morning|afternoon|evening))\b",
        r"\bcall\s+(me\s+)?(back\s+)?next\s+(week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\bcall\s+(me\s+)?(back\s+)?(at|around)\s+\d{1,2}\s*(am|pm)\b",
        r"\bin\s+\d+\s+(minute|minutes|hour|hours|day|days|week|weeks)\b",
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
    ("failed_booking", [
        r"\bdidn'?t\s+work\b",
        r"\bcouldn'?t\s+book\b",
        r"\bunable\s+to\s+(book|schedule)\b",
        r"\bbooking\s+(failed|didn'?t|didn\s+t)\b",
        r"\b(booking|appointment|schedule|link)\b.*\btry\s+again\b",
        r"\blink\s+(didn'?t|doesn'?t|not)\s+work\b",
        r"\bsomething\s+went\s+wrong\b",
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

    # "in N minutes"
    m = re.search(r"\bin\s+(\d+)\s+minutes?\b", text, re.IGNORECASE)
    if m:
        return now + timedelta(minutes=int(m.group(1)))

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
    # Clamp to valid 12-hour range; out-of-range values come from noisy transcripts
    hour_12 = max(1, min(hour_12, 12))
    minute  = max(0, min(minute, 59))
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

# Threshold constants (can be overridden in tests)
_LOW_CONFIDENCE_MIN_CHARS: int = 5    # transcript shorter than this → low_confidence_audio
_PARTIAL_ENGAGEMENT_MAX_SECONDS: int = 120  # call shorter than this → partial_engagement eligible


def detect_intent(
    transcript: str,
    *,
    executed_actions: list | None = None,
    duration_seconds: int | None = None,
) -> dict[str, Any] | None:
    """
    Detect the primary intent from a call transcript.

    Extended to support live-call signals via executed_actions and duration_seconds.
    Both parameters are optional — existing callers that omit them are unaffected.

    Priority order:
      1. executed_actions overrides (transfer / booking — highest priority)
      2. low_confidence_audio  (length/noise check — BEFORE patterns)
      3. Pattern-based intents (do_not_call → request_email)
         including human_transfer_request, failed_booking
      4. partial_engagement    (post-pattern fallback when nothing matched)

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

    stripped = transcript.strip()
    normalized = stripped.lower()

    # ── 1. Executed-actions overrides — highest priority, fire before all checks ─
    # When Synthflow reports a definitive action (transfer, booking), trust it
    # regardless of transcript length.
    ea_flags = _extract_executed_actions(executed_actions)
    if ea_flags["transfer_attempted"]:
        if ea_flags["transfer_failed"] and not ea_flags["transfer_succeeded"]:
            # The caller asked for a human and the transfer did NOT connect (2026-10-23: carrier-blocked, outside hours).
            # Not a live handoff: no "a human took over" assumption; a staff callback task is raised after the call.
            logger.info("live_call_detected: transfer FAILED via executed_actions | target=%s reason=%s",
                        ea_flags["transfer_target"], ea_flags["transfer_reason"])
            return {"intent": "human_transfer_request", "confidence": 0.95,
                    "entities": {"datetime": None, "channel": None, "transfer_attempted": False, "transfer_failed": True,
                                 "transfer_target": ea_flags["transfer_target"], "transfer_reason": ea_flags["transfer_reason"]}}
        logger.info("live_call_detected: transfer_attempted via executed_actions")
        return {"intent": "human_transfer_request", "confidence": 0.95, "entities": {"datetime": None, "channel": None, "transfer_attempted": True}}

    if ea_flags["booking_success"]:
        # An appointment already exists in the calendar. It used to be reported as callback_with_time, which scheduled an AI redial
        # for someone who had just booked (spec/42). It keeps the same urgency everywhere else (sales queue, guard, alerts).
        logger.info("booking_success_detected: booked via executed_actions")
        return {"intent": "booked", "confidence": 0.95, "entities": {"datetime": None, "channel": None, "booked": True}}

    if ea_flags["booking_failed"] and not ea_flags["booking_success"]:
        logger.info("failed_booking_detected: booking_failed via executed_actions")
        return {"intent": "failed_booking", "confidence": 0.95, "entities": {"datetime": None, "channel": None}}

    # ── 1b. Carrier / phone-system recording — NOT a person, never a callback request (spec/41) ──
    if is_carrier_message(stripped):
        logger.info("carrier_message_detected: the 'human' was a carrier / phone-system recording")
        return {"intent": "carrier_message", "confidence": 0.95, "entities": {"datetime": None, "channel": None, "carrier_message": True}}

    # ── 1c. Answering-machine greeting — no person, so no intent at all (spec/42) ──
    human_norm_text = human_text(stripped)
    if human_norm_text is not None and is_voicemail_greeting(human_norm_text):
        logger.info("voicemail_greeting_detected: the 'human' was an answering machine - no intent")
        return None

    # ── 2. Low-confidence audio — checked BEFORE patterns ────────────────────
    if len(stripped) < _LOW_CONFIDENCE_MIN_CHARS:
        logger.info("low_confidence_audio_detected: transcript too short (%d chars)", len(stripped))
        return {"intent": "low_confidence_audio", "confidence": 0.8, "entities": {"datetime": None, "channel": None}}

    _LOW_CONFIDENCE_PATTERNS = [
        re.compile(r"^\s*\.{2,}\s*$"),          # only dots
        re.compile(r"^\s*\[?noise\]?\s*$", re.IGNORECASE),
        re.compile(r"^\s*\[?inaudible\]?\s*$", re.IGNORECASE),
        re.compile(r"^\s*\[?silence\]?\s*$", re.IGNORECASE),
        re.compile(r"^\s*\[?background\s+noise\]?\s*$", re.IGNORECASE),
    ]
    for pat in _LOW_CONFIDENCE_PATTERNS:
        if pat.match(stripped):
            logger.info("low_confidence_audio_detected: noise/empty-like transcript")
            return {"intent": "low_confidence_audio", "confidence": 0.8, "entities": {"datetime": None, "channel": None}}

    # ── 3. Pattern-based intent matching ─────────────────────────────────────
    human_norm = human_norm_text.lower() if human_norm_text is not None else None
    for intent, patterns in _COMPILED:
        target = human_norm if (human_norm is not None and intent in _CALLBACK_FAMILY) else normalized
        for pattern in patterns:
            if pattern.search(target):
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

    # ── 4. Partial engagement — post-pattern fallback ─────────────────────────
    # Fires when: transcript exists, nothing matched, and duration_seconds is
    # explicitly provided (caller has real call metadata).
    # Requires either a short call OR a short transcript with a known short duration.
    # Does NOT fire when duration_seconds is None — that would produce false positives
    # on transcripts that simply don't match any known intent.
    if duration_seconds is not None:
        is_short_call = duration_seconds < _PARTIAL_ENGAGEMENT_MAX_SECONDS
        if is_short_call or len(stripped) < 300:
            logger.info(
                "partial_engagement_detected: no strong intent, "
                "transcript_len=%d duration=%s",
                len(stripped), duration_seconds,
            )
            return {
                "intent": "partial_engagement",
                "confidence": 0.6,
                "entities": {"datetime": None, "channel": None},
            }

    return None
