"""
Backfill: set GHL "AI Campaign" = No for leads Cora has already marked do-not-call or invalid (spec/43).

Until 2026-10-26 the do-not-call / not-interested / wrong-number handlers tried to write AI Campaign = No to GHL but the write ran in shadow
mode (and phone-keyed contacts could not be updated by id), so GHL still says Yes for most of them (48 of the 54 most recent do-not-call
leads). This finds every lead_state row with do_not_call or invalid set, looks the contact up in GHL, and for those whose AI Campaign is not
already No:

  dry run (default): prints counts and the list (phone, reason, current value). Writes nothing.
  --apply          : writes AI Campaign = No through the same code the handlers now use (live-mode flags, phone verified against the
                     GHL contact), then READS EACH CONTACT BACK from GHL and reports any that did not take.

Idempotent: contacts already at No are skipped, so it is safe to re-run. A contact GHL cannot match (phone differs / not found) is reported
and never written.

Usage (inside the worker container, which has the GHL credentials):

    docker compose exec worker-default python execution/backfill_ai_campaign_off.py                 # dry run
    docker compose exec worker-default python execution/backfill_ai_campaign_off.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def field_value(contact: dict[str, Any], field_id: str | None) -> str:
    """The contact's AI Campaign value as a plain string ('Yes' / 'No' / 'None' when unset)."""
    v = next((f.get("value") for f in (contact.get("customFields") or []) if f.get("id") == field_id), None)
    v = v[0] if isinstance(v, list) and v else v
    return "None" if v in (None, "", []) else str(v)


def candidates(session: Any, days: int | None) -> list[tuple[str, str]]:
    """(contact_id, reason) for every lead Cora marked do-not-call or invalid (optionally only updated in the last `days`)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import or_, select

    from app.models.lead_state import LeadState

    q = select(LeadState.contact_id, LeadState.do_not_call, LeadState.invalid).where(or_(LeadState.do_not_call.is_(True), LeadState.invalid.is_(True)))
    if days:
        q = q.where(LeadState.updated_at >= datetime.now(tz=timezone.utc) - timedelta(days=days))
    return [(cid, "do_not_call" if dnc else "invalid") for cid, dnc, _inv in session.execute(q).all() if cid]


def run(session: Any, settings: Any, ghl: Any, flags: Any, apply: bool = False, days: int | None = None, out=print) -> dict[str, int]:
    from app.core import intent_actions as ia
    from app.worker.jobs.crm_jobs import _resolve_to_field_ids

    label = getattr(settings, "ghl_field_ai_campaign", None) or "AI Campaign"
    field_id = next(iter(_resolve_to_field_ids(ghl, {label: "No"}) or {}), None)
    if not field_id:
        out(f"STOP: the GHL field {label!r} could not be found; nothing to do.")
        return {"leads": 0, "to_fix": 0, "written": 0, "verified": 0, "failed": 0, "unmatched": 0}
    leads = candidates(session, days)
    seen: set[str] = set()
    todo: list[tuple[str, str, str, str]] = []          # contact_id, reason, ghl_id, current value
    already_no = unmatched = 0
    for cid, reason in leads:
        if cid in seen:
            continue
        seen.add(cid)
        ghl_id = ia.resolve_ghl_contact_id(ghl, cid)
        if not ghl_id:
            unmatched += 1
            continue
        current = field_value((lambda c: (c or {}).get("contact", c) or {})(ghl.get_contact(ghl_id)), field_id)
        if current == "No":
            already_no += 1
        else:
            todo.append((cid, reason, ghl_id, current))
    out(f"leads marked do-not-call / invalid in Cora: {len(seen)}")
    out(f"  already AI Campaign = No in GHL: {already_no}")
    out(f"  to set to No: {len(todo)}  (by reason {dict(Counter(r for _c, r, _g, _v in todo))}; current values {dict(Counter(v for *_x, v in todo))})")
    out(f"  no matching GHL contact (never written): {unmatched}")
    result = {"leads": len(seen), "to_fix": len(todo), "written": 0, "verified": 0, "failed": 0, "unmatched": unmatched}
    if not apply:
        for cid, reason, _g, current in todo:
            out(f"    {cid} | {reason} | now {current}")
        out("DRY RUN - nothing was changed. Re-run with --apply.")
        return result
    for cid, reason, ghl_id, _current in todo:
        try:
            ok = ia.set_ai_campaign_no(ghl, cid, settings, flags, f"backfill:{reason}")
        except Exception as exc:
            out(f"  FAILED {cid}: {type(exc).__name__}")
            result["failed"] += 1
            continue
        if not ok:
            result["failed"] += 1
            out(f"  NOT WRITTEN {cid} (shadow mode or unmatched)")
            continue
        result["written"] += 1
        back = field_value((lambda c: (c or {}).get("contact", c) or {})(ghl.get_contact(ghl_id)), field_id)
        if back == "No":
            result["verified"] += 1
        else:
            result["failed"] += 1
            out(f"  READ-BACK MISMATCH {cid}: GHL says {back!r}")
    out(f"APPLIED: written {result['written']}, verified by read-back {result['verified']}, failed {result['failed']}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="really write (default is a dry run)")
    ap.add_argument("--days", type=int, default=None, help="only leads updated in the last N days (default: all)")
    args = ap.parse_args(argv)

    from app.adapters.ghl import GHLClient
    from app.config import get_settings
    from app.core.mode_flags import get_mode_flags
    from app.db import get_sync_session

    settings = get_settings()
    with get_sync_session() as session:
        flags = get_mode_flags(session, settings)
        if args.apply and not flags.ghl_writes_enabled:
            print("STOP: GHL writes are in shadow mode in the dashboard (System Controls); nothing would be written.")
            return 2
        run(session, settings, GHLClient(settings=settings), flags, args.apply, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
