"""Unit tests for app/core/schedule_context.py and its prompt wiring (spec/32). No I/O."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.core.schedule_context import (
    DEFAULT_FREE_SIGNUP_URL,
    build_schedule_block,
    date_has_passed,
    is_unset,
)

TODAY = date(2026, 11, 13)


# ── is_unset ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", ["", "  ", None, "upcoming", "TBD", "none", "N/A"])
def test_unset_values(v):
    assert is_unset(v)


@pytest.mark.parametrize("v", ["November 12, 2026", "Q3 2026", "Oct 29"])
def test_set_values(v):
    assert not is_unset(v)


# ── date_has_passed ──────────────────────────────────────────────────────────

def test_day_after_the_date_has_passed():
    assert date_has_passed("November 12, 2026", TODAY)


def test_on_the_day_itself_not_passed():
    assert not date_has_passed("November 13, 2026", TODAY)


def test_future_date_not_passed():
    assert not date_has_passed("November 14, 2026", TODAY)


@pytest.mark.parametrize("v", ["Oct 29", "October 29", "Q3 2026", "upcoming", "", None])
def test_yearless_or_non_date_never_auto_expires(v):
    assert not date_has_passed(v, TODAY)


def test_numeric_with_year():
    assert date_has_passed("10/29/2026", TODAY)


# ── build_schedule_block ─────────────────────────────────────────────────────

def test_both_set_lists_dates_and_rsvp_no_override():
    b = build_schedule_block("November 12, 2026", "October 29, 2026", "https://rsvp.test")
    assert "Next class: November 12, 2026" in b
    assert "Next Open House: October 29, 2026" in b
    assert "Open House RSVP: https://rsvp.test" in b
    assert "OVERRIDE" not in b and "myfreeaiclass" not in b


def test_both_unset_overrides_and_points_to_free_signup():
    b = build_schedule_block("", "", "https://rsvp.test")
    assert "SCHEDULE OVERRIDE" in b
    assert DEFAULT_FREE_SIGNUP_URL in b
    assert "www.myfreeaiclass.com" in b
    assert "https://rsvp.test" not in b          # RSVP for a dead event is never sent
    assert "Next class:" not in b and "Next Open House:" not in b


def test_only_open_house_expired_keeps_class_drops_rsvp():
    b = build_schedule_block("November 12, 2026", "", "https://rsvp.test")
    assert "Next class: November 12, 2026" in b
    assert "https://rsvp.test" not in b
    assert "NO upcoming Open House" in b and "NO upcoming class" not in b
    assert DEFAULT_FREE_SIGNUP_URL in b


def test_only_class_expired_keeps_open_house():
    b = build_schedule_block("", "October 29, 2026", "https://rsvp.test")
    assert "Next Open House: October 29, 2026" in b and "https://rsvp.test" in b
    assert "NO upcoming class start date" in b


# ── prompt wiring: every tier renders with both states ───────────────────────

def _ctx(cfg):
    from app.core.ai_message_generator import _load_brand_context

    return _load_brand_context(None, SimpleNamespace(**cfg))


@pytest.mark.parametrize("version", ["tier_1", "tier_2", "tier_3", "tier_final"])
def test_every_tier_renders_active_and_expired(version):
    from app.prompts.families import vm_followup_generator  # noqa: F401
    from app.prompts.registry import get_prompt

    entry = get_prompt("vm_followup_generator", version)
    common = dict(lead_first_name="Sam", campaign_name="c", video_transcripts="", prior_messages="",
                  ghl_conversation_thread="")

    active = entry.build_messages(**common, **_ctx({
        "next_class_start": "November 12, 2026", "next_open_house_date": "October 29, 2026",
        "live_open_house_link": "https://rsvp.test"}))[1]["content"]
    assert "Next class: November 12, 2026" in active and "OVERRIDE" not in active

    expired = entry.build_messages(**common, **_ctx({
        "next_class_start": "", "next_open_house_date": "",
        "live_open_house_link": "https://rsvp.test"}))[1]["content"]
    assert "SCHEDULE OVERRIDE" in expired
    assert "www.myfreeaiclass.com" in expired
    assert "https://rsvp.test" not in expired
    assert "November 12" not in expired and "October 29" not in expired


def test_brand_context_masks_unset_values():
    ctx = _ctx({"next_class_start": "upcoming", "next_open_house_date": "",
                "live_open_house_link": "https://rsvp.test"})
    assert ctx["next_class_start"] == "(none scheduled)"
    assert ctx["next_open_house_date"] == "(none scheduled)"
    assert ctx["live_open_house_link"] == ""
