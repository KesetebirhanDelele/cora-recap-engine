"""
Per-person outbound attempt cap (spec/54).

Measured 2026-11-21 (new AI class, bookings recorded): attempts 7 and later are 62% of calls and 63% of minutes
but 23% of bookings (0.13% booking rate against 1.38% for a first attempt). Kes decided 2026-11-22 to cap Cora's
AI outbound attempts per person at 6, counted over the person's lifetime (the same count the analysis used), with
one exception and one test group:

  * Exception ("AI-class guarantee"): anyone with fewer than 3 attempts since the AI class started
    (outbound_attempt_cap_era_start, default 2026-08-23) may still be called, so people dialed many times under the
    old Data Analytics pitch still hear the new pitch up to 3 times.
  * Holdout: a fixed share of phone numbers (default 10%, chosen by a hash of the number, so the same person is always
    in or out) is never capped. It is the comparison group for the cap, to be read after 4 to 6 weeks.

An "attempt" is one outbound ColdLead/NewLead row in call_events (answered or not), the same definition the
analysis used. The count is keyed on contact_id = the person's phone number, which is how outbound calls are stored;
older rows with no contact_id are not counted, so the cap errs toward allowing a call.

Fails OPEN: any lookup problem logs a warning and the call proceeds.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

AI_AGENTS = ("ColdLead", "NewLead")


@dataclass(frozen=True)
class CapDecision:
    blocked: bool
    reason: str  # allowed_under_cap | allowed_ai_guarantee | allowed_holdout | blocked_at_cap
    lifetime: int
    since_era: int


def phone_variants(phone: str) -> list[str]:
    """The spellings a US number can be stored under in call_events.contact_id: +1XXXXXXXXXX, 1XXXXXXXXXX, XXXXXXXXXX."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return []
    ten = digits[-10:]
    return [f"+1{ten}", f"1{ten}", ten]


def in_holdout(phone: str, percent: int) -> bool:
    """True when this number is in the uncapped comparison group. Deterministic: the same number is always in or out."""
    if percent <= 0:
        return False
    digits = re.sub(r"\D", "", phone or "")[-10:]
    if len(digits) < 10:
        return False
    bucket = int(hashlib.sha256(digits.encode()).hexdigest()[:8], 16) % 100
    return bucket < min(percent, 100)


def decide(lifetime: int, since_era: int, *, cap: int, guarantee: int, holdout: bool) -> CapDecision:
    """Pure rule. Blocked only when the lifetime count has reached the cap AND the person already had the guaranteed
    number of calls since the AI class started AND they are not in the uncapped holdout."""
    if holdout:
        return CapDecision(False, "allowed_holdout", lifetime, since_era)
    if lifetime < cap:
        return CapDecision(False, "allowed_under_cap", lifetime, since_era)
    if since_era < guarantee:
        return CapDecision(False, "allowed_ai_guarantee", lifetime, since_era)
    return CapDecision(True, "blocked_at_cap", lifetime, since_era)


def count_attempts(session: Any, phone: str, era_start: datetime) -> tuple[int, int]:
    """(lifetime attempts, attempts since era_start) for this number. Portable SQL (works on SQLite in tests)."""
    variants = phone_variants(phone)
    if not variants:
        return 0, 0
    stmt = text(
        """
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN COALESCE(call_started_at, created_at) >= :era THEN 1 ELSE 0 END), 0)
        FROM call_events
        WHERE contact_id IN :variants
          AND report_excluded = :false
          AND LOWER(direction) = 'outbound'
          AND voice_agent IN :agents
        """
    ).bindparams(bindparam("variants", expanding=True), bindparam("agents", expanding=True))
    row = session.execute(
        stmt, {"variants": variants, "agents": list(AI_AGENTS), "era": era_start, "false": False}
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def _cfg(settings: Any) -> Optional[dict[str, Any]]:
    """Validated settings, or None when the cap is off or the settings are not usable (fail open)."""
    enabled = getattr(settings, "outbound_attempt_cap_enabled", False)
    cap = getattr(settings, "outbound_attempt_cap", None)
    guarantee = getattr(settings, "outbound_attempt_cap_era_guarantee", None)
    percent = getattr(settings, "outbound_attempt_cap_holdout_percent", None)
    era = getattr(settings, "outbound_attempt_cap_era_start", None)
    if enabled is not True or not all(isinstance(v, int) and not isinstance(v, bool) for v in (cap, guarantee, percent)):
        return None
    if not isinstance(era, str):
        return None
    try:
        era_dt = datetime.fromisoformat(era).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {"cap": cap, "guarantee": guarantee, "percent": percent, "era": era_dt}


def check_attempt_cap(session: Any, phone: str, settings: Any) -> Optional[dict[str, Any]]:
    """Return ``{"lifetime", "since_era", "cap"}`` when this person has reached the cap and must not be dialed, else ``None``.
    Never raises."""
    cfg = _cfg(settings)
    if cfg is None or not phone:
        return None
    try:
        lifetime, since_era = count_attempts(session, phone, cfg["era"])
        d = decide(
            lifetime, since_era, cap=cfg["cap"], guarantee=cfg["guarantee"],
            holdout=in_holdout(phone, cfg["percent"]),
        )
        if not d.blocked:
            return None
        return {"lifetime": d.lifetime, "since_era": d.since_era, "cap": cfg["cap"]}
    except Exception as exc:  # noqa: BLE001 - a cap must never break dialing
        logger.warning("check_attempt_cap: lookup failed (failing open) | phone=<redacted>: %s", exc)
        return None
