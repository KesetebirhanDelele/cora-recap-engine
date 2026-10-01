"""
Wrong-date guard — pure logic, no I/O.

Decides whether an outbound SMS/email told a lead the wrong *next class start*
or *next open house* date. Dates that belong to something else (a call-back,
an appointment) are deliberately ignored.

How a date mention is classified
--------------------------------
Every date in the text (``Oct 29``, ``November 12, 2026``, ``10/29``) is given
a kind by the keyword CLOSEST to it inside the same sentence:

  - class keywords        (class, cohort, program starts, ...)      -> "class"
  - open-house keywords   (open house, info session, RSVP, ...)     -> "open_house"
  - appointment keywords  (call you, appointment, scheduled, ...)   -> "appointment"
  - nothing nearby                                                   -> "other"

Only "class" and "open_house" dates are compared with the configured values;
"appointment" and "other" are never flagged. On an exact distance tie the
class/open-house keyword wins (a wrong program date is the costlier miss).

Comparison rules
----------------
- Configured values come from app_config (free text, e.g. "November 12, 2026").
  expected=None, or free text that is not a date ("Q3 2026"), disables that one
  check - we never guess.
- expected="" means NOTHING is scheduled (the date expired / was cleared): any
  dated class / open-house mention is wrong, because past dates must not be told.
- A mention without a year matches on month+day only. A mention with a year
  must also match the configured year when the configured value has one.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date

KIND_CLASS = "class"
KIND_OPEN_HOUSE = "open_house"
KIND_APPOINTMENT = "appointment"
KIND_OTHER = "other"

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "Oct 29", "October 29th, 2026", "Nov. 12 2026"
_TEXT_DATE = re.compile(
    rf"\b(?P<mon>{_MONTH_ALT})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
# "29 October 2026", "29th of October"
_DAY_FIRST_DATE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH_ALT})\b\.?"
    r"(?:,?\s+(?P<year>20\d{2}))?",
    re.IGNORECASE,
)
# "10/29", "10/29/2026", "10/29/26"
_NUMERIC_DATE = re.compile(
    r"(?<![\d/])(?P<mon>1[0-2]|0?[1-9])/(?P<day>3[01]|[12]\d|0?[1-9])"
    r"(?:/(?P<year>20\d{2}|\d{2}))?(?![\d/])"
)

_CLASS_KW = re.compile(
    r"\bclass(?:es)?\b|\bcohort\b|\bstart(?:s|ing)?\s+date\b|\bkick(?:s|ing)?[\s-]?off\b"
    r"|\b(?:program|training|course|bootcamp|accelerator)\s+(?:starts?|begins?|launch\w*)\b"
    r"|\benroll\w*\s+(?:by|before|deadline)\b",
    re.IGNORECASE,
)
_OPEN_HOUSE_KW = re.compile(
    r"\bopen[\s-]?house\b|\binfo(?:rmation)?\s+session\b|\brsvp\b|\bwebinar\b|\beventbrite\b",
    re.IGNORECASE,
)
_APPOINTMENT_KW = re.compile(
    r"\bappointment\b|\bcall(?:ing)?\s+(?:you|back)\b|\bcall\s*-?back\b|\bcallback\b"
    r"|\bschedul\w*\b|\bconsult\w*\b|\bmeeting\b|\bspeak\s+(?:with|to)\b"
    r"|\btalk\s+(?:with|to)\b|\breach(?:ing)?\s+out\b|\bfollow(?:ing)?[\s-]?up\b"
    r"|\bphone\s+(?:call|chat)\b|\bbook(?:ed|ing)?\b|\bchat\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\n+")
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ParsedDate:
    month: int
    day: int
    year: int | None = None

    def label(self) -> str:
        base = f"{_MONTH_NAMES[self.month]} {self.day}"
        return f"{base}, {self.year}" if self.year else base


_MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November",
    12: "December",
}


@dataclass(frozen=True)
class DateMention:
    parsed: ParsedDate
    kind: str
    raw: str


@dataclass(frozen=True)
class WrongDate:
    kind: str            # KIND_CLASS | KIND_OPEN_HOUSE
    raw: str             # text as it appeared in the message
    expected: str        # configured value, as typed on the dashboard


def _valid(month: int, day: int) -> bool:
    try:
        date(2000, month, day)  # leap-year base so Feb 29 is accepted
        return True
    except ValueError:
        return False


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    y = int(raw)
    return 2000 + y if y < 100 else y


def parse_config_date(value: str | None) -> ParsedDate | None:
    """Parse a dashboard value like 'November 12, 2026'. None if not a date."""
    if not value:
        return None
    mentions = _find_spans(value)
    return mentions[0][2] if mentions else None


def clean_text(body: str | None, subject: str | None = None) -> str:
    """Flatten email HTML / entities so dates split across tags still parse."""
    parts = [p for p in (subject, body) if p]
    text = " ".join(parts)
    text = _TAG.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text)


def _find_spans(text: str) -> list[tuple[int, int, ParsedDate]]:
    found: list[tuple[int, int, ParsedDate]] = []
    taken: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < te and e > ts for ts, te in taken)

    for pattern in (_TEXT_DATE, _DAY_FIRST_DATE, _NUMERIC_DATE):
        for m in pattern.finditer(text):
            mon_raw = m.group("mon")
            month = int(mon_raw) if mon_raw.isdigit() else _MONTHS[mon_raw.lower()]
            day = int(m.group("day"))
            if not _valid(month, day) or overlaps(m.start(), m.end()):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.end(), ParsedDate(month, day, _year(m.group("year")))))
    return sorted(found, key=lambda t: t[0])


def _nearest_kind(sentence: str, start: int, end: int) -> str:
    """Kind of the keyword closest to the date span inside one sentence."""
    best_kind, best_dist = KIND_OTHER, None
    # Order = tie-break priority: class / open_house before appointment.
    for kind, pattern in (
        (KIND_CLASS, _CLASS_KW),
        (KIND_OPEN_HOUSE, _OPEN_HOUSE_KW),
        (KIND_APPOINTMENT, _APPOINTMENT_KW),
    ):
        for m in pattern.finditer(sentence):
            if m.end() <= start:
                dist = start - m.end()
            elif m.start() >= end:
                dist = m.start() - end
            else:
                dist = 0
            if best_dist is None or dist < best_dist:
                best_kind, best_dist = kind, dist
    return best_kind


def extract_date_mentions(text: str) -> list[DateMention]:
    """All dates in `text`, each classified by its nearest keyword."""
    mentions: list[DateMention] = []
    offset = 0
    for sentence in _SENTENCE_SPLIT.split(text):
        idx = text.find(sentence, offset)
        offset = idx + len(sentence) if idx >= 0 else offset
        for s, e, parsed in _find_spans(sentence):
            mentions.append(
                DateMention(parsed=parsed, kind=_nearest_kind(sentence, s, e), raw=sentence[s:e])
            )
    return mentions


def _matches(mention: ParsedDate, expected: ParsedDate) -> bool:
    if (mention.month, mention.day) != (expected.month, expected.day):
        return False
    if mention.year is not None and expected.year is not None:
        return mention.year == expected.year
    return True


def find_wrong_dates(
    body: str | None,
    subject: str | None,
    *,
    expected_class_start: str | None,
    expected_open_house: str | None,
) -> list[WrongDate]:
    """
    Return every class-start / open-house date in the message that disagrees
    with the configured value. Empty list = message is fine (or nothing
    checkable). Appointment / unrelated dates never appear in the result.
    """
    expected = {
        KIND_CLASS: (parse_config_date(expected_class_start), expected_class_start),
        KIND_OPEN_HOUSE: (parse_config_date(expected_open_house), expected_open_house),
    }
    wrong: list[WrongDate] = []
    for mention in extract_date_mentions(clean_text(body, subject)):
        if mention.kind not in expected:
            continue
        exp_parsed, exp_raw = expected[mention.kind]
        if exp_raw is None:
            continue  # check disabled
        if exp_parsed is None:
            if exp_raw.strip() == "":  # nothing scheduled -> any date is wrong
                wrong.append(WrongDate(kind=mention.kind, raw=mention.raw, expected=""))
            continue  # free text that isn't a date - can't judge
        if not _matches(mention.parsed, exp_parsed):
            wrong.append(WrongDate(kind=mention.kind, raw=mention.raw, expected=exp_raw))
    return wrong
