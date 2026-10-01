"""
Pre-send SMS gate - pure, no I/O (spec/34).

EVERY text Cora originates (follow-ups, date corrections, test sends) is checked here BEFORE it is
written to GHL, because in GHL that write is what sends the text. The Twilio sole-proprietor A2P 10DLC
brand is limited to (Twilio docs, 2026-10):

  * 1,000 message SEGMENTS per day to T-Mobile (day resets 00:00 US Pacific). Over the cap Twilio
    rejects with error 30023 and never retries.  -> Cora hard-caps at 999 segments per Pacific day,
    counting every carrier (conservative) - app_config `sms_daily_segment_cap`, never above 999.
  * 1 message/second per carrier, ~15 messages/minute to AT&T.  -> min gap + per-minute cap below.

A text that does not fit today's budget is DEFERRED to the next Pacific day (at an hour that is legal
for the lead), never dropped and never sent late at night.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core import offer
from app.core.sms_eligibility import TCPA_EARLIEST_HOUR, in_tcpa_hours
from app.core.sms_links import disallowed_links, parse_allowed_domains

PACIFIC = ZoneInfo("America/Los_Angeles")

HARD_MAX_DAILY_SEGMENTS = 999       # Kes 2026-10-01: never above 999 / Pacific day, whatever app_config says
DEFAULT_DAILY_SEGMENTS = 999
DEFAULT_MIN_GAP_SECONDS = 5         # 1 MPS with margin
DEFAULT_PER_MINUTE_CAP = 12         # AT&T ~15/min with margin
DEFAULT_MAX_SEGMENTS_PER_MESSAGE = 2     # Kes 2026-10-02: one segment if possible, at most two
MIN_WORDS = 3                       # a real sentence - blocks stray tokens such as "warm_lead"
WARN_FRACTION = 0.8                 # alert when this much of the day's budget is used

ALLOW, BLOCK, DEFER = "allow", "block", "defer"

# GSM-03.38 basic set; the extension set costs 2 septets each.
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXT = set("^{}\\[~]|€\f")
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{%|%\}|\b(?:None|null|undefined|nan)\b|\[\[|<[a-z/][^>]*>", re.I)


_SMART = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
          "–": "-", "—": "-", "−": "-", "…": "...", " ": " ", " ": " ", "​": "",
          "•": "-", "·": "-"}


def normalize_sms(text: str | None) -> str:
    """Plain GSM-7 text: curly quotes / dashes / ellipsis -> ASCII, emoji and other non-GSM characters dropped.
    One curly apostrophe switches a text to UCS-2 (70 chars per segment) - a 180-character text would bill 3 segments."""
    import unicodedata

    out = []
    for ch in text or "":
        ch = _SMART.get(ch, ch)
        for c in ch:
            if c in _GSM_BASIC or c in _GSM_EXT:
                out.append(c)
            else:
                folded = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
                out.append(folded)
    return re.sub(r"[ 	]{2,}", " ", "".join(out)).strip()


def count_segments(text: str | None) -> int:
    """Billable SMS segments for `text` (GSM-7: 160 / 153 per part; otherwise UCS-2: 70 / 67)."""
    t = text or ""
    if not t:
        return 0
    if all(c in _GSM_BASIC or c in _GSM_EXT for c in t):
        units = sum(2 if c in _GSM_EXT else 1 for c in t)
        return 1 if units <= 160 else -(-units // 153)
    units = len(t.encode("utf-16-le")) // 2
    return 1 if units <= 70 else -(-units // 67)


def pacific_day_start_utc(now: datetime) -> datetime:
    local = now.astimezone(PACIFIC)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def next_pacific_day_start_utc(now: datetime) -> datetime:
    local = now.astimezone(PACIFIC)
    nxt = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, hours=3)).replace(
        hour=0, minute=0, second=0, microsecond=0)   # +27h then floor: correct across DST changes
    return nxt.astimezone(timezone.utc)


def next_legal_send_time(earliest: datetime, tz_name: str) -> datetime:
    """First instant >= `earliest` whose hour in the lead's own timezone is inside the TCPA hours."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # unknown tz -> treat as US/Central, the strictest-in-practice default
        tz = ZoneInfo("America/Chicago")
    local = earliest.astimezone(tz)
    if in_tcpa_hours(local.hour):
        return earliest
    if local.hour >= TCPA_EARLIEST_HOUR:                       # evening: next morning
        local = (local + timedelta(days=1))
    return local.replace(hour=TCPA_EARLIEST_HOUR, minute=5, second=0, microsecond=0).astimezone(timezone.utc)


@dataclass(frozen=True)
class GateConfig:
    daily_cap: int = DEFAULT_DAILY_SEGMENTS
    min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS
    per_minute_cap: int = DEFAULT_PER_MINUTE_CAP
    max_segments_per_message: int = DEFAULT_MAX_SEGMENTS_PER_MESSAGE
    allowed_link_domains: str = ""
    expected_class_start: str | None = None
    expected_open_house: str | None = None
    forbidden_terms: tuple = ()               # retired course names (offer.py) - never in any SMS
    notification_only: bool = False           # follow-up SMS: missed-call / reminder notices, no marketing

    @property
    def effective_daily_cap(self) -> int:
        return max(0, min(int(self.daily_cap), HARD_MAX_DAILY_SEGMENTS))


@dataclass(frozen=True)
class GateUsage:
    """What the ledger says about the current Pacific day / last minute."""
    segments_today: int = 0
    seconds_since_last: float | None = None
    sent_last_minute: int = 0


@dataclass(frozen=True)
class GateDecision:
    action: str                       # allow | block | defer
    segments: int
    code: str = "ok"                  # ok | content:* | daily_cap | min_gap | per_minute
    reason: str = ""
    retry_after_seconds: float | None = None   # min_gap / per_minute
    next_day: bool = False                     # daily_cap


def content_violations(text: str | None, cfg: GateConfig) -> list[str]:
    """Rule breaks that make the text unsendable as an SMS (empty list = clean)."""
    body = (text or "").strip()
    out: list[str] = []
    if not body:
        return ["empty message"]
    if len(body.split()) < MIN_WORDS:
        out.append("not a real sentence (stray token such as a classification tag?)")
    if _PLACEHOLDER.search(body):
        out.append("unfilled template placeholder / markup")
    if disallowed_links(body, parse_allowed_domains(cfg.allowed_link_domains)):
        out.append("contains a link (links are not allowed in SMS)")
    if count_segments(body) > cfg.max_segments_per_message:
        out.append(f"longer than {cfg.max_segments_per_message} segments")
    hits = offer.forbidden_hits(body, cfg.forbidden_terms)
    if hits:
        out.append("names a course we no longer offer: " + ", ".join(hits))
    if cfg.notification_only:
        m = offer.marketing_hit(body)
        if m:
            out.append(f"marketing wording in a notification SMS: {m!r}")
    if cfg.expected_class_start is not None or cfg.expected_open_house is not None:
        from app.core.wrong_date_guard import find_wrong_dates

        wrong = find_wrong_dates(
            body, "", expected_class_start=cfg.expected_class_start, expected_open_house=cfg.expected_open_house)
        if wrong:
            out.append("wrong class/open-house date: " + ", ".join(w.raw for w in wrong))
    return out


def decide(text: str | None, usage: GateUsage, cfg: GateConfig) -> GateDecision:
    """The gate. Order: content rules (BLOCK) -> daily segment budget (DEFER to next day) ->
    provider pacing (DEFER a few seconds). Nothing is sent unless this returns ALLOW."""
    segments = count_segments(text)
    problems = content_violations(text, cfg)
    if problems:
        return GateDecision(BLOCK, segments, "content:" + problems[0].split(":")[0].replace(" ", "_")[:40],
                            "; ".join(problems))
    cap = cfg.effective_daily_cap
    if usage.segments_today + segments > cap:
        return GateDecision(
            DEFER, segments, "daily_cap",
            f"daily SMS budget used ({usage.segments_today}/{cap} segments, Pacific day) - moved to next day",
            next_day=True)
    if usage.seconds_since_last is not None and usage.seconds_since_last < cfg.min_gap_seconds:
        return GateDecision(
            DEFER, segments, "min_gap", "pacing: <%ss since the previous SMS" % cfg.min_gap_seconds,
            retry_after_seconds=max(cfg.min_gap_seconds - usage.seconds_since_last, 1.0) + 0.5)
    if cfg.per_minute_cap > 0 and usage.sent_last_minute >= cfg.per_minute_cap:
        return GateDecision(
            DEFER, segments, "per_minute", f"pacing: {usage.sent_last_minute} SMS in the last minute",
            retry_after_seconds=30.0)
    return GateDecision(ALLOW, segments)


def usage_level(segments_today: int, cap: int) -> str:
    """ok | warning (>=80%) | exhausted (nothing more fits)."""
    if cap <= 0 or segments_today >= cap:
        return "exhausted"
    return "warning" if segments_today >= cap * WARN_FRACTION else "ok"


def config_from(getter: Any, session: Any, settings: Any, class_start: str | None = None,
                open_house: str | None = None) -> GateConfig:
    """Build a GateConfig from app_config via `getter(key, session, settings, default)` (app_config.get_str)."""
    def num(key: str, default: float) -> float:
        try:
            return float(getter(key, session, settings, str(default)))
        except (TypeError, ValueError):
            return default

    return GateConfig(
        daily_cap=int(num("sms_daily_segment_cap", DEFAULT_DAILY_SEGMENTS)),
        min_gap_seconds=num("sms_min_gap_seconds", DEFAULT_MIN_GAP_SECONDS),
        per_minute_cap=int(num("sms_per_minute_cap", DEFAULT_PER_MINUTE_CAP)),
        max_segments_per_message=int(num("sms_max_segments_per_message", DEFAULT_MAX_SEGMENTS_PER_MESSAGE)),
        allowed_link_domains=getter("sms_allowed_link_domains", session, settings, "") or "",
        forbidden_terms=offer.parse_terms(getter("offer_forbidden_terms", session, settings, offer.DEFAULT_FORBIDDEN_TERMS)),
        expected_class_start=class_start,
        expected_open_house=open_house,
    )
