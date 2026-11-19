"""
Did a LIVE PERSON answer? (spec/49 addendum, 2026-11-19)

`staff_call_quality.call_connected` is only "completed and >= 20 s", so a voicemail the rep left a message on
counts as connected. Whisper transcripts carry no speaker labels, so no phrase list is reliable ("not available"
appears in both screening and real answers). A small LLM judge reads the transcript instead.

Fail-safe rules (a wrongly counted voicemail hides a lead from the rep for 7 days; a missed real conversation only
means the rep may re-call someone):
  * judge error / bad answer  -> None (stored as NULL, retried later; never counts)
  * "unclear"                 -> False
  * empty transcript          -> False
  * inbound calls are never judged and never count as "staff reached them" (direction must be outbound)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TRANSCRIPT_CAP = 6000

PROMPT = """You label one outbound sales-call transcript (speech-to-text, no speaker labels, may be messy).
The caller is a staff member from Colaberry calling a lead.
Decide: did a LIVE HUMAN on the lead's end actually answer and take part in the conversation?
- "voicemail": a greeting/recording/phone system (e.g. "leave a message", "after the tone", "not available", "please leave your name and number", a business greeting) and the caller just leaves a message, with no live reply from a person. Call-screening ("I'll see if this person is available") followed by "not available" is voicemail.
- "human": a real person replied live (answers questions, says not interested, asks to call back, confirms details, etc.), even if the conversation was short or negative.
- "unclear": cannot tell.
Return JSON: {"label": "voicemail"|"human"|"unclear", "reason": "<=12 words"}.

Transcript:
"""


def judge_human_answer(transcript: str | None, client: Any, model: str) -> bool | None:
    """True = live person, False = voicemail/unclear/empty, None = judge failed (retry later)."""
    if not (transcript or "").strip():
        return False
    try:
        out = client.chat_completion(
            messages=[{"role": "user", "content": PROMPT + transcript[:TRANSCRIPT_CAP]}],
            model=model, response_format={"type": "json_object"},
        )
        label = out.get("label") if isinstance(out, dict) else None
    except Exception as exc:  # network / quota / bad JSON: leave unjudged, never guess
        logger.warning("human_answer_judge: judge failed (%s)", type(exc).__name__)
        return None
    if label == "human":
        return True
    if label in ("voicemail", "unclear"):
        return False
    return None


def reached_conditions(S: Any) -> list[Any]:
    """SQL conditions for 'staff really reached this contact': an outbound, connected call a live person answered.
    Use this (not call_connected alone) anywhere 'contacted' matters."""
    return [S.call_connected.is_(True), S.direction == "outbound", S.answered_by_human.is_(True)]


def judge_pending(
    session: Session, settings: Any, *, since: datetime, limit: int = 300, client: Any = None,
) -> dict[str, int]:
    """Judge outbound connected calls that have a transcript but no verdict yet (newest first, bounded).
    Idempotent and safe to rerun; caller commits. Failures stay NULL and are retried on the next run."""
    from app.models.staff_call_quality import StaffCallQuality as S

    if client is None:
        from app.adapters.openai_client import OpenAIClient

        client = OpenAIClient(settings=settings)
    model = getattr(settings, "openai_model_human_answer", None) or "gpt-4o-mini"
    rows = session.execute(
        select(S).where(
            S.call_connected.is_(True), S.direction == "outbound", S.answered_by_human.is_(None),
            S.transcript_text.is_not(None), S.transcript_text != "", S.call_time >= since,
        ).order_by(S.call_time.desc()).limit(limit)
    ).scalars().all()
    counts = {"judged": 0, "human": 0, "not_human": 0, "failed": 0}
    for row in rows:
        verdict = judge_human_answer(row.transcript_text, client, model)
        if verdict is None:
            counts["failed"] += 1
            continue
        row.answered_by_human = verdict
        counts["judged"] += 1
        counts["human" if verdict else "not_human"] += 1
    session.flush()
    return counts
