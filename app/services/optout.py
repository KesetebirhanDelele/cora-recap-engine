"""
Opt-out handling: turn what a lead SAID into real GHL DND (spec/36).

Sources: answered-call transcripts (the lead's own lines), inbound SMS / email replies read by
delivery_sync, and a one-time reconciliation of leads Cora already marked do-not-call.
Decision (Kes, 2026-10-01): DND follows the lead's wording (calls / texts / email / all); clear opt-outs
are applied automatically (an LLM may judge free-form replies, only >= 0.85 confidence); everything else goes
to the review list; every action is recorded in `optout_actions` with the previous GHL state so it can be undone.
GHL DND is the hard stop - GHL's own workflows and senders honour it; Cora's flags alone do not reach them.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import optout as oo
from app.core.sms_eligibility import BLOCK, cora_state_verdict, is_stop_reply

logger = logging.getLogger(__name__)

LLM_MIN_CONFIDENCE = 0.85
TRIAGE_DISMISS_MIN = 0.8        # LLM says "not an opt-out" at >= this -> dismissed (reason kept on the row)
SHORT_REPLY_CHARS = 200          # "not interested"/"wrong number" auto-close only on short replies
_monotonic = time.monotonic


def _cfg(session: Session, settings: Any, key: str, default: str) -> str:
    from app.core.app_config import get_str

    return get_str(key, session, settings, default)


def _scope_str(scope) -> str:
    return ",".join(sorted(scope))


def _scope_set(value: str | None) -> set[str]:
    return {x for x in (value or "").split(",") if x}


def _excerpt(body: str | None) -> str:
    return " ".join((body or "").split())[:300]


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── ghl helpers ──────────────────────────────────────────────────────────────

def _ghl(settings: Any) -> Any:
    from app.adapters.ghl import GHLClient

    return GHLClient(settings=settings)


def dnd_channels(record: dict | None) -> set[str]:
    """Channels with active GHL DND on a contact record."""
    c = (record or {}).get("contact", record or {})
    if c.get("dnd") is True:
        return set(oo.ALL)
    out = set()
    for ghl_name, ch in (("Call", "call"), ("SMS", "sms"), ("Email", "email")):
        v = (c.get("dndSettings") or {}).get(ghl_name) or {}
        if isinstance(v, dict) and str(v.get("status", "")).lower() in ("active", "permanent"):
            out.add(ch)
    return out


def _resolve(ghl: Any, contact_id: str) -> tuple[str, dict]:
    from app.services.wrong_date_monitor import _lookup_contact

    return _lookup_contact(ghl, contact_id)


# ── records ──────────────────────────────────────────────────────────────────

def _insert(session: Session, *, contact_id: str, source: str, external_id: str | None, kind: str, scope,
            confidence: str, decided_by: str, status: str, phrase: str = "", excerpt: str = "", reason: str = "",
            previous: dict | None = None) -> str | None:
    """Insert one action row; returns its id, or None when (source, external_id) was already recorded."""
    rid = str(uuid.uuid4())
    res = session.execute(
        text("""
            INSERT INTO optout_actions (id, contact_id, source, external_id, kind, scope, confidence, decided_by,
                                        status, phrase, excerpt, reason, previous_state, created_at, resolved_at)
            VALUES (:id, :c, :src, :x, :k, :sc, :cf, :by, :st, :ph, :ex, :rs, CAST(:pv AS jsonb), :now,
                    :ra)
            ON CONFLICT (source, external_id) WHERE external_id IS NOT NULL DO NOTHING
        """),
        {"id": rid, "c": contact_id, "src": source, "x": external_id, "k": kind, "sc": _scope_str(scope),
         "cf": confidence, "by": decided_by, "st": status, "ph": phrase[:200], "ex": excerpt[:300],
         "rs": reason[:300], "pv": json.dumps(previous) if previous is not None else None, "now": _now(),
         "ra": _now() if status in ("applied", "ok", "shadow") else None})
    return rid if (res.rowcount or 0) > 0 else None


def _audit(session: Session, action: str, entity_id: str, ctx: dict, operator: str = "system") -> None:
    session.execute(
        text("""INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
                VALUES (:id, 'optout', :e, :a, :op, CAST(:c AS jsonb), :n)"""),
        {"id": str(uuid.uuid4()), "e": entity_id, "a": action, "op": operator, "c": json.dumps(ctx), "n": _now()})


# ── Cora-side effects ────────────────────────────────────────────────────────

def _lead_snapshot(session: Session, contact_id: str) -> dict | None:
    row = session.execute(text("SELECT contact_id, do_not_call, status FROM lead_state WHERE contact_id = :c OR normalized_phone = :c LIMIT 1"),
                          {"c": contact_id}).fetchone()
    return {"contact_id": row[0], "do_not_call": bool(row[1]), "status": row[2]} if row else None


def _stop_cora_outreach(session: Session, settings: Any, contact_id: str, scope: set[str], kind: str) -> dict | None:
    """Cora's own records: calls stop (do_not_call) for any call-scope opt-out; wrong number / not interested close the lead."""
    from app.core import intent_actions as ia

    prior = _lead_snapshot(session, contact_id)
    lead_id = (prior or {}).get("contact_id") or contact_id
    if kind == oo.DND and oo.CALL in scope:
        ia._update_lead_state(session, lead_id, do_not_call=True, status="closed")
        ia._cancel_contact_jobs(session, lead_id, exclude_job_id="")
    elif kind == oo.WRONG_NUMBER:
        ia._handle_wrong_number(session, lead_id, "", {}, settings)
        ia._cancel_contact_jobs(session, lead_id, exclude_job_id="")
    elif kind == oo.NOT_INTERESTED:
        ia._handle_not_interested(session, lead_id, "", {}, settings)
        ia._cancel_contact_jobs(session, lead_id, exclude_job_id="")
    return prior


def _already(session: Session, source: str, external_id: str | None) -> bool:
    """True when this reply / call was already handled (so a replay never writes to GHL twice)."""
    if not external_id:
        return False
    return session.execute(text("SELECT 1 FROM optout_actions WHERE source = :s AND external_id = :x"),
                           {"s": source, "x": external_id}).fetchone() is not None


# ── applying ─────────────────────────────────────────────────────────────────

def apply_dnd(session: Session, settings: Any, ghl: Any, *, contact_id: str, scope, source: str,
              external_id: str | None, phrase: str, excerpt: str, decided_by: str = "auto",
              confidence: str = oo.HIGH, record: dict | None = None, action_id: str | None = None) -> str:
    """Set GHL DND for `scope`, update Cora, record + audit. Returns the action status."""
    from app.core.mode_flags import get_mode_flags

    scope = frozenset(scope)
    flags = get_mode_flags(session, settings)
    if not flags.ghl_writes_enabled:
        if not action_id:
            _insert(session, contact_id=contact_id, source=source, external_id=external_id, kind=oo.DND, scope=scope,
                    confidence=confidence, decided_by=decided_by, status="shadow", phrase=phrase, excerpt=excerpt,
                    reason="GHL writes are off (shadow mode)")
        return "shadow"
    attempts, prior_state = 0, None
    if action_id:
        row = session.execute(text("SELECT previous_state FROM optout_actions WHERE id = :i"), {"i": action_id}).fetchone()
        prior_state = (json.loads(row[0]) if isinstance(row[0], str) else row[0]) if row and row[0] else None
        attempts = int((prior_state or {}).get("attempts", 0))
    operator_click = decided_by not in ("auto", "llm")
    try:
        real_id, rec = (contact_id, record) if record is not None else _resolve(ghl, contact_id)
        before = dnd_channels(rec)
        todo = set(scope) - before          # GHL refuses to overwrite a PERMANENT DND (e.g. an earlier STOP): only add what is missing
        previous = {"ghl_dnd": sorted(before), "ghl_contact_id": real_id}
        if todo:
            ghl.set_dnd(real_id, todo, active=True, reason=f"Lead opt-out ({source}): {phrase}", mode_flags=flags)
        previous["lead_state"] = _stop_cora_outreach(session, settings, real_id, set(scope), oo.DND)
        status, reason = "applied", ("" if todo else "already DND in GHL for every requested channel")
    except Exception as exc:
        logger.error("optout: apply failed | contact=%s: %s", contact_id, exc)
        real_id = contact_id
        reason = ("apply failed: " + str(exc))[:280]
        if operator_click:
            status, previous = "review", prior_state            # stays on the tile with the reason; not lost
        else:
            status, previous = "failed", {"attempts": attempts + 1}   # retried by retry_failed (max 5)
    if action_id:
        session.execute(text("""UPDATE optout_actions SET status=:st, scope=:sc, decided_by=:by, reason=:rs, resolved_at=:n,
                                previous_state=CAST(:pv AS jsonb), contact_id=:c WHERE id=:id"""),
                        {"st": status, "sc": _scope_str(scope), "by": decided_by, "rs": reason, "n": _now(),
                         "pv": json.dumps(previous) if previous is not None else None, "c": real_id, "id": action_id})
        rid = action_id
    else:
        rid = _insert(session, contact_id=real_id, source=source, external_id=external_id, kind=oo.DND, scope=scope,
                      confidence=confidence, decided_by=decided_by, status=status, phrase=phrase, excerpt=excerpt,
                      reason=reason, previous=previous)
    if rid:
        _audit(session, "optout_" + status, rid, {"scope": sorted(scope), "source": source, "by": decided_by})
    return status


def _propose(session: Session, *, contact_id: str, scope, source: str, external_id: str | None, phrase: str,
             excerpt: str, reason: str, kind: str = oo.DND) -> None:
    _insert(session, contact_id=contact_id, source=source, external_id=external_id, kind=kind, scope=scope,
            confidence=oo.MEDIUM, decided_by="auto", status="review", phrase=phrase, excerpt=excerpt, reason=reason)


# ── LLM judge for free-form replies ──────────────────────────────────────────

_PROMPT = (
    "You judge whether a lead's reply to an admissions follow-up asks us to STOP contacting them. "
    "Reply JSON: {\"decision\": one of opt_out_all|opt_out_call|opt_out_sms|opt_out_email|not_interested|wrong_number|other, "
    "\"confidence\": 0-1, \"reason\": short}. opt_out_* only when the lead clearly asks not to be contacted "
    "(on all channels or the named one). A question, complaint without a request to stop, out-of-office text, or "
    "interest in the program is 'other'. Be conservative."
)


def llm_judge(settings: Any, body: str, channel: str) -> tuple[str, float, str] | None:
    try:
        from app.adapters.openai_client import OpenAIClient

        out = OpenAIClient(settings=settings).chat_completion(
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": f"Channel: {channel}\nReply: {body[:600]}"}],
            model=getattr(settings, "openai_model_consent_detector", "gpt-4o-mini"),
            response_format={"type": "json_object"}, max_tokens=120)
        return str(out.get("decision", "other")), float(out.get("confidence", 0)), str(out.get("reason", ""))[:200]
    except Exception as exc:
        logger.warning("optout: LLM judge unavailable: %s", exc)
        return None


_DECISION_SCOPE = {"opt_out_all": set(oo.ALL), "opt_out_call": {oo.CALL}, "opt_out_sms": {oo.SMS},
                   "opt_out_email": {oo.EMAIL}}


# ── entry points ─────────────────────────────────────────────────────────────

def handle_reply(session: Session, settings: Any, ghl: Any, *, channel: str, message: dict, contact_id: str,
                 llm_budget: list[int] | None = None) -> str:
    """Classify one inbound SMS / email reply (message = GHL message dict). Never raises. Returns what happened."""
    try:
        with session.begin_nested():             # a DB error here must not poison the caller's transaction
            return _handle_reply(session, settings, ghl, channel, message, contact_id, llm_budget)
    except Exception as exc:
        logger.error("optout.handle_reply failed: %s", exc)
        return "error"


def _handle_reply(session: Session, settings: Any, ghl: Any, channel: str, message: dict, contact_id: str,
                  llm_budget: list[int] | None) -> str:
    body = message.get("body") or ""
    ext = message.get("id")
    if _already(session, channel + "_reply", ext):
        return "duplicate"
    cl = oo.classify(body, channel)
    cleaned = oo.clean_reply(body, channel)
    ex = _excerpt(cleaned)
    if cl.kind == oo.NONE:
        return "none"
    ghl = ghl or _ghl(settings)        # the contacts-scoped client: the conversations token cannot edit contacts
    if cl.kind == oo.DND and cl.confidence == oo.HIGH:
        return apply_dnd(session, settings, ghl, contact_id=contact_id, scope=cl.scope, source=channel + "_reply",
                         external_id=ext, phrase=cl.phrase, excerpt=ex)
    if cl.kind in (oo.NOT_INTERESTED, oo.WRONG_NUMBER):
        if len(cleaned) > SHORT_REPLY_CHARS:
            _propose(session, contact_id=contact_id, scope=set(), source=channel + "_reply", external_id=ext,
                     phrase=cl.phrase, excerpt=ex, reason="long reply - confirm", kind=cl.kind)
            return "review"
        prior = _stop_cora_outreach(session, settings, contact_id, set(), cl.kind)
        rid = _insert(session, contact_id=contact_id, source=channel + "_reply", external_id=ext, kind=cl.kind,
                      scope=set(), confidence=oo.HIGH, decided_by="auto", status="applied", phrase=cl.phrase,
                      excerpt=ex, previous={"lead_state": prior})
        if rid:
            _audit(session, "optout_applied", rid, {"kind": cl.kind, "source": channel + "_reply"})
        return "applied"
    # unclear: ask the LLM (bounded), else review
    if _cfg(session, settings, "optout_llm_enabled", "true").lower() in ("true", "1", "yes") \
            and (llm_budget is None or llm_budget[0] > 0):
        if llm_budget is not None:
            llm_budget[0] -= 1
        verdict = llm_judge(settings, cleaned, channel)
        if verdict:
            decision, conf, why = verdict
            if decision in _DECISION_SCOPE and conf >= LLM_MIN_CONFIDENCE:
                return apply_dnd(session, settings, ghl, contact_id=contact_id, scope=_DECISION_SCOPE[decision],
                                 source=channel + "_reply", external_id=ext, phrase=f"LLM: {why}", excerpt=ex,
                                 decided_by="llm", confidence=oo.MEDIUM)
            if decision == "other" and conf >= LLM_MIN_CONFIDENCE:
                return "none"
            if decision in _DECISION_SCOPE:
                _propose(session, contact_id=contact_id, scope=_DECISION_SCOPE[decision], source=channel + "_reply",
                         external_id=ext, phrase=f"LLM {conf:.2f}: {why}", excerpt=ex, reason="LLM not sure enough")
                return "review"
    _propose(session, contact_id=contact_id, scope={channel}, source=channel + "_reply", external_id=ext,
             phrase=cl.phrase, excerpt=ex, reason="wording unclear - needs a human")
    return "review"


def handle_call(session: Session, settings: Any, *, contact_id: str, call_event_id: str,
                transcript: str | None) -> str:
    """Called when Cora's call check found do_not_call. Applies GHL DND by the lead's own words. Never raises."""
    try:
        with session.begin_nested():
            return _handle_call(session, settings, contact_id, call_event_id, transcript)
    except Exception as exc:
        logger.error("optout.handle_call failed: %s", exc)
        return "error"


def _handle_call(session: Session, settings: Any, contact_id: str, call_event_id: str, transcript: str | None) -> str:
    if _already(session, "call", call_event_id):
        return "duplicate"
    cl = oo.classify_call(transcript)
    ghl = _ghl(settings)
    if cl.kind == oo.DND and cl.confidence == oo.HIGH:
        return apply_dnd(session, settings, ghl, contact_id=contact_id, scope=cl.scope, source="call",
                         external_id=call_event_id, phrase=cl.phrase,
                         excerpt=_excerpt(oo.human_lines(transcript)[-300:]))
    # Cora's regex fired but not on the lead's own lines (e.g. the agent said it) -> a human decides
    _propose(session, contact_id=contact_id, scope=set(oo.ALL), source="call", external_id=call_event_id,
             phrase="not found in the lead's own words", excerpt=_excerpt(oo.human_lines(transcript)[-300:]),
             reason="do-not-call detected on the call but not in the lead's lines")
    return "review"


def retry_failed(session: Session, settings: Any, ghl: Any | None = None, limit: int = 5) -> int:
    """Re-apply automatic opt-outs whose GHL write failed (max 5 attempts each). Returns how many succeeded."""
    rows = session.execute(text("""
        SELECT id, contact_id, scope, source, phrase, excerpt, decided_by FROM optout_actions
        WHERE status = 'failed' AND kind = 'dnd' AND source <> 'reconcile'
          AND coalesce((previous_state->>'attempts')::int, 0) < 5
        ORDER BY created_at LIMIT :n"""), {"n": limit}).fetchall()
    ghl = ghl or _ghl(settings)
    fixed = 0
    for rid, cid, scope, source, phrase, excerpt, by in rows:
        st = apply_dnd(session, settings, ghl, contact_id=cid, scope=_scope_set(scope), source=source, external_id=None,
                       phrase=phrase or "", excerpt=excerpt or "", decided_by=by or "auto", action_id=rid)
        fixed += 1 if st == "applied" else 0
    session.commit()
    return fixed


def reconcile_step(session: Session, settings: Any, ghl: Any | None = None, limit: int = 20,
                   budget_seconds: float = 15.0) -> dict[str, int]:
    """Leads Cora marked do-not-call that have no optout_actions row yet: compare with GHL DND. Already DND ->
    'ok'; missing -> a review row (scope from the lead's words on the call, else all channels)."""
    ghl = ghl or _ghl(settings)
    rows = session.execute(text("""
        SELECT l.contact_id, l.normalized_phone FROM lead_state l
        WHERE l.do_not_call AND NOT EXISTS (
            SELECT 1 FROM optout_actions a WHERE a.source = 'reconcile' AND a.external_id = l.contact_id)
        ORDER BY l.updated_at DESC LIMIT :n"""), {"n": limit}).fetchall()
    out = {"checked": 0, "ok": 0, "review": 0, "failed": 0}
    started = _monotonic()
    for cid, phone in rows:
        if _monotonic() - started > budget_seconds:
            break
        try:
            real, rec = _resolve(ghl, cid)
        except Exception as exc:
            _insert(session, contact_id=cid, source="reconcile", external_id=cid, kind=oo.DND, scope=set(),
                    confidence=oo.MEDIUM, decided_by="auto", status="failed", reason=f"GHL contact not readable: {exc}"[:280])
            out["failed"] += 1
            continue
        tr = session.execute(text("""SELECT transcript FROM call_events WHERE detected_intent = 'do_not_call'
                                     AND (contact_id = :c OR contact_id = :p) ORDER BY created_at DESC LIMIT 1"""),
                             {"c": cid, "p": phone or cid}).scalar()
        cl = oo.classify_call(tr)
        need = set(cl.scope) if (cl.kind == oo.DND and cl.scope) else set(oo.ALL)
        missing = need - dnd_channels(rec)
        out["checked"] += 1
        if not missing:
            _insert(session, contact_id=real, source="reconcile", external_id=cid, kind=oo.DND, scope=need,
                    confidence=oo.HIGH, decided_by="auto", status="ok", phrase=cl.phrase, reason="already DND in GHL")
            out["ok"] += 1
        else:
            _insert(session, contact_id=real, source="reconcile", external_id=cid, kind=oo.DND, scope=need,
                    confidence=oo.HIGH if cl.kind == oo.DND else oo.MEDIUM, decided_by="auto", status="review",
                    phrase=cl.phrase or "no explicit wording found - defaulting to all channels",
                    excerpt=_excerpt(oo.human_lines(tr)[-300:]), reason="Cora marked do-not-call; GHL has no DND for " + _scope_str(missing))
            out["review"] += 1
    session.commit()
    return out


# ── operator actions (review tile) ───────────────────────────────────────────

def _get(session: Session, action_id: str) -> dict | None:
    r = session.execute(text("SELECT id, contact_id, source, kind, scope, status, phrase, excerpt, previous_state "
                             "FROM optout_actions WHERE id = :i"), {"i": action_id}).fetchone()
    return dict(zip(("id", "contact_id", "source", "kind", "scope", "status", "phrase", "excerpt", "previous"), r)) if r else None


def review_apply(session: Session, settings: Any, action_id: str, operator: str, scope: set[str] | None = None,
                 ghl: Any | None = None) -> str:
    a = _get(session, action_id)
    if not a or a["status"] != "review":
        raise ValueError("not awaiting review")
    chosen = scope if scope else _scope_set(a["scope"]) or set(oo.ALL)
    if a["kind"] in (oo.NOT_INTERESTED, oo.WRONG_NUMBER):
        prior = _stop_cora_outreach(session, settings, a["contact_id"], set(), a["kind"])
        session.execute(text("UPDATE optout_actions SET status='applied', decided_by=:by, resolved_at=:n, "
                             "previous_state=CAST(:pv AS jsonb) WHERE id=:i"),
                        {"by": operator, "n": _now(), "pv": json.dumps({"lead_state": prior}), "i": action_id})
        return "applied"
    return apply_dnd(session, settings, ghl or _ghl(settings), contact_id=a["contact_id"], scope=chosen,
                     source=a["source"], external_id=None, phrase=a["phrase"] or "", excerpt=a["excerpt"] or "",
                     decided_by=operator, action_id=action_id)


def review_dismiss(session: Session, action_id: str, operator: str) -> None:
    done = session.execute(text("UPDATE optout_actions SET status='dismissed', decided_by=:by, resolved_at=:n "
                                "WHERE id=:i AND status='review' RETURNING id"),
                           {"by": operator, "n": _now(), "i": action_id}).fetchone()
    if not done:
        raise ValueError("not awaiting review")
    _audit(session, "optout_dismissed", action_id, {}, operator)


def undo(session: Session, settings: Any, action_id: str, operator: str, ghl: Any | None = None) -> None:
    """Remove the DND this action added (channels that were already DND before stay) and restore Cora's lead flags."""
    from app.core.mode_flags import get_mode_flags

    a = _get(session, action_id)
    if not a or a["status"] != "applied" or a["kind"] != oo.DND:
        raise ValueError("only an applied DND can be undone")
    prev = a["previous"] if isinstance(a["previous"], dict) else json.loads(a["previous"] or "{}")
    ours = _scope_set(a["scope"]) - set(prev.get("ghl_dnd") or [])
    ghl = ghl or _ghl(settings)
    flags = get_mode_flags(session, settings)
    if ours:
        ghl.set_dnd(prev.get("ghl_contact_id") or a["contact_id"], ours, active=False, reason="Undone by operator", mode_flags=flags)
        if ours >= set(oo.ALL):
            ghl.set_dnd(prev.get("ghl_contact_id") or a["contact_id"], set(oo.ALL), active=False, mode_flags=flags)
    ls = prev.get("lead_state")
    if ls:
        session.execute(text("UPDATE lead_state SET do_not_call=:d, status=:s, version=version+1, updated_at=:n WHERE contact_id=:c"),
                        {"d": ls["do_not_call"], "s": ls["status"], "n": _now(), "c": ls["contact_id"]})
    session.execute(text("UPDATE optout_actions SET status='undone', resolved_at=:n WHERE id=:i"), {"n": _now(), "i": action_id})
    _audit(session, "optout_undone", action_id, {"channels": sorted(ours)}, operator)


def apply_batch(session: Session, settings: Any, operator: str, source: str = "reconcile",
                budget_seconds: float = 18.0, max_items: int = 15) -> dict[str, int]:
    """Apply the proposed DND for waiting review rows of one source (the tile loops until remaining == 0)."""
    ids = [r[0] for r in session.execute(text(
        "SELECT id FROM optout_actions WHERE status='review' AND source=:s AND kind='dnd' "
        "AND coalesce(reason,'') NOT LIKE 'apply failed:%' ORDER BY created_at LIMIT :n"),
        {"s": source, "n": max_items}).fetchall()]
    ghl = _ghl(settings)
    started, done, failed = _monotonic(), 0, 0
    for i in ids:
        if _monotonic() - started > budget_seconds:
            break
        try:
            st = review_apply(session, settings, i, operator, ghl=ghl)
            session.commit()
            done += 1 if st in ("applied", "shadow") else 0
            failed += 1 if st in ("failed", "review") else 0
        except Exception as exc:
            session.rollback()
            failed += 1
            logger.error("optout batch item failed: %s", exc)
    remaining = session.execute(text("SELECT count(*) FROM optout_actions WHERE status='review' AND source=:s AND kind='dnd' "
                                     "AND coalesce(reason,'') NOT LIKE 'apply failed:%'"),
                                {"s": source}).scalar() or 0
    return {"applied": done, "failed": failed, "remaining": int(remaining)}


# ── safeguard used by Cora's own sends ───────────────────────────────────────

def cora_block_reason(session: Session, contact_ids: list[str], channel: str) -> str | None:
    """Why Cora itself must not send on `channel` to this lead: its own do-not-contact / closed flags, a STOP reply,
    or an opt-out (applied OR still awaiting review) covering the channel. GHL DND is checked separately."""
    ids = [i for i in {c for c in contact_ids if c}]
    if not ids:
        return None
    lead = session.execute(text("""SELECT contact_id, do_not_call, invalid, status, sales_outcome, last_replied_at
                                   FROM lead_state WHERE contact_id = ANY(:i) OR normalized_phone = ANY(:i) LIMIT 1"""),
                           {"i": ids}).fetchone()
    lead_d = dict(zip(("contact_id", "do_not_call", "invalid", "status", "sales_outcome", "last_replied_at"), lead)) if lead else None
    if lead_d:
        ids = list({*ids, lead_d["contact_id"]})
    bodies = session.execute(text("SELECT body FROM inbound_messages WHERE contact_id = ANY(:i) ORDER BY received_at DESC LIMIT 20"),
                             {"i": ids}).fetchall()
    stop = channel == "sms" and any(is_stop_reply(b[0]) for b in bodies)
    if lead_d:
        lead_d = {**lead_d, "last_replied_at": None}               # a reply alone is not an opt-out
    v = cora_state_verdict(lead_d, stop)
    if v is not None and v.action == BLOCK:
        return v.reason
    rows = session.execute(text("""SELECT kind, scope, status FROM optout_actions
                                   WHERE contact_id = ANY(:i) AND status IN ('applied','review')"""), {"i": ids}).fetchall()
    for kind, scope, status in rows:
        if kind in (oo.NOT_INTERESTED, oo.WRONG_NUMBER) and status == "applied":
            return f"lead said {kind.replace('_', ' ')}"
        if kind == oo.DND and channel in _scope_set(scope):
            return "lead opted out of " + channel + (" (awaiting review)" if status == "review" else "")
    return None


# ── dashboard reads ──────────────────────────────────────────────────────────

def _looks_phone(v: str) -> bool:
    d = (v or "").replace("+", "").replace("-", "").replace(" ", "")
    return d.isdigit() and len(d) >= 10


def fill_contact_info(session: Session, rows: list[dict], ghl: Any | None, budget_seconds: float = 10.0,
                      max_lookups: int = 40) -> None:
    """Give every row the lead's FULL phone / email (stored on the row, so each lookup happens once). A contact id that
    is already a phone number needs no lookup. Parallel and time-boxed; failures are retried on the next refresh."""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    started = time.monotonic()
    todo: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("contact_phone") or r.get("contact_email"):
            continue
        if _looks_phone(r["contact_id"]):
            r["contact_phone"] = r["contact_id"]
            session.execute(text("UPDATE optout_actions SET contact_phone = :p WHERE id = :i"), {"p": r["contact_id"], "i": r["id"]})
        else:
            todo.setdefault(r["contact_id"], []).append(r)
    if not todo or ghl is None:
        return

    def fetch(cid: str) -> tuple[str, str, str]:
        rec = ghl.get_contact(cid)
        c = rec.get("contact", rec) if isinstance(rec, dict) else {}
        return cid, c.get("phone") or "", c.get("email") or ""

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, c) for c in list(todo)[:max_lookups]]
        try:
            for f in as_completed(futures, timeout=budget_seconds + 5):
                try:
                    cid, phone, email = f.result(timeout=max(budget_seconds - (time.monotonic() - started), 0.1))
                except Exception:
                    continue
                for r in todo[cid]:
                    r["contact_phone"], r["contact_email"] = phone, email
                    session.execute(text("UPDATE optout_actions SET contact_phone = :p, contact_email = :e WHERE id = :i"),
                                    {"p": phone, "e": email, "i": r["id"]})
        except Exception as exc:                       # timeout: show what we have
            logger.warning("optout contact lookup incomplete: %s", exc)


def triage_unclear(session: Session, settings: Any, limit: int = 10) -> dict[str, int]:
    """Word matching only NOMINATES a line ("leave me a message", "not in my spam" hit the hint words). Before a human
    reads it, an LLM reads the quote: clearly not an opt-out (>= 0.8) -> dismissed with the reason on record; otherwise
    its verdict is attached to the row and the human decides."""
    rows = session.execute(text("""
        SELECT id, source, excerpt FROM optout_actions
        WHERE status = 'review' AND kind = 'dnd' AND confidence = 'medium'
          AND coalesce(reason, '') ILIKE '%unclear%' AND coalesce(reason, '') NOT LIKE '%[LLM%' AND coalesce(excerpt, '') <> ''
        ORDER BY created_at LIMIT :n"""), {"n": limit}).fetchall()
    out = {"dismissed": 0, "annotated": 0, "skipped": 0}
    for rid, source, excerpt in rows:
        channel = "call" if "call" in source else ("email" if "email" in source else "sms")
        v = llm_judge(settings, excerpt, channel)
        if not v:
            out["skipped"] += 1
            continue
        decision, conf, why = v
        note = f"[LLM {conf:.2f}] {decision}: {why}"[:290]
        if decision == "other" and conf >= TRIAGE_DISMISS_MIN:
            session.execute(text("UPDATE optout_actions SET status='dismissed', decided_by='llm', resolved_at=:n, reason=:r WHERE id=:i"),
                            {"n": _now(), "r": "not an opt-out " + note[:270], "i": rid})
            _audit(session, "optout_dismissed", rid, {"by": "llm", "confidence": conf})
            out["dismissed"] += 1
        else:
            scope = ",".join(sorted(_DECISION_SCOPE.get(decision, set()))) if decision in _DECISION_SCOPE and conf >= 0.7 else None
            session.execute(text("UPDATE optout_actions SET reason=:r, scope=coalesce(:sc, scope) WHERE id=:i"),
                            {"r": note, "sc": scope, "i": rid})
            out["annotated"] += 1
    session.commit()
    return out


def snapshot(session: Session, ghl: Any | None = None) -> dict[str, Any]:
    def rows(sql: str, **p: Any) -> list[dict]:
        cols = ("id", "contact_id", "source", "kind", "scope", "confidence", "decided_by", "status", "phrase",
                "excerpt", "reason", "created_at", "resolved_at", "contact_phone", "contact_email")
        return [{**dict(zip(cols, r)), "created_at": r[11].isoformat() if r[11] else None,
                 "resolved_at": r[12].isoformat() if r[12] else None,
                 "contact_phone": r[13] or "", "contact_email": r[14] or ""}
                for r in session.execute(text(sql), p).fetchall()]

    base = ("SELECT id, contact_id, source, kind, scope, confidence, decided_by, status, phrase, excerpt, reason, "
            "created_at, resolved_at, contact_phone, contact_email FROM optout_actions ")
    counts = {r[0]: int(r[1]) for r in session.execute(text(
        "SELECT status, count(*) FROM optout_actions GROUP BY status")).fetchall()}
    by_source = {r[0]: int(r[1]) for r in session.execute(text(
        "SELECT source, count(*) FROM optout_actions WHERE status='review' GROUP BY source")).fetchall()}
    review = rows(base + "WHERE status='review' ORDER BY created_at DESC LIMIT 100")
    applied = rows(base + "WHERE status='applied' ORDER BY resolved_at DESC NULLS LAST LIMIT 50")
    failed = rows(base + "WHERE status='failed' AND source <> 'reconcile' ORDER BY created_at DESC LIMIT 20")
    fill_contact_info(session, review + applied + failed, ghl)
    return {
        "counts": counts, "review_by_source": by_source,
        "review": review, "applied": applied, "failed": failed,
        "reconcile_pending": int(session.execute(text("""SELECT count(*) FROM lead_state l WHERE l.do_not_call AND NOT EXISTS
            (SELECT 1 FROM optout_actions a WHERE a.source='reconcile' AND a.external_id = l.contact_id)""")).scalar() or 0),
    }
