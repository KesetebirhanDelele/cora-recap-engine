"""Orientation dates for the voice prompts (Ali 2026-11-10): computed, never hardcoded, never expiring."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.core import orientation as o

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("today,want", [
    (date(2026, 11, 10), "Thursday, November 19"),                       # today: November's one-off
    (date(2026, 11, 19), "Thursday, November 19"),                       # on the day itself
    (date(2026, 11, 20), "Monday, December 7, 6:30 to 8:00 PM Central"),  # once Thursday passes
    (date(2026, 12, 1), "Monday, December 7, 6:30 to 8:00 PM Central"),
    (date(2026, 12, 7), "Monday, December 7, 6:30 to 8:00 PM Central"),
    (date(2026, 12, 8), "Monday, January 4, 6:30 to 8:00 PM Central"),   # then the next first Monday
    (date(2027, 1, 5), "Monday, February 1, 6:30 to 8:00 PM Central"),
    (date(2027, 12, 31), "Monday, January 3, 6:30 to 8:00 PM Central"),  # across the year end
])
def test_next_orientation(today, want):
    assert o.orientation_text(today) == want


def test_it_never_runs_out_of_a_date_for_the_next_five_years():
    d = date(2026, 11, 10)
    for _ in range(365 * 5):
        nxt, _monthly = o.next_orientation(d)
        assert nxt >= d and (nxt.weekday() == 0 or nxt in o.ONE_OFF)
        d = date.fromordinal(d.toordinal() + 1)


def test_first_monday():
    assert o.first_monday(2026, 12) == date(2026, 12, 7) and o.first_monday(2027, 3) == date(2027, 3, 1)


def test_placeholder_is_rendered_in_central_time_and_plain_text_is_untouched():
    late_utc = datetime(2026, 11, 20, 3, 0, tzinfo=ZoneInfo("UTC"))      # still Nov 19 in Chicago
    assert o.render_placeholders("Next: {{NEXT_ORIENTATION}}.", late_utc) == "Next: Thursday, November 19."
    next_day = datetime(2026, 11, 20, 18, 0, tzinfo=ZoneInfo("UTC"))
    assert "December 7" in o.render_placeholders("{{NEXT_ORIENTATION}}", next_day)
    assert o.render_placeholders("no placeholder", late_utc) == "no placeholder"


# ── the prompts ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_voice_prompts_have_no_class_start_date_and_use_the_computed_orientation():
    from app.adapters.synthflow import _load_campaign_prompt

    for campaign in ("Cold Lead", "New Lead"):
        p = _load_campaign_prompt(campaign)
        assert "{{NEXT_ORIENTATION}}" not in p                              # always filled in
        assert "November 30" not in p and "Next class start" not in p and "cohort's classes" not in p
        assert "There is NO class start date" in p and "as soon as you enroll" in p
        assert "first Monday of the month after you join" in p
    docs = (ROOT / "docs" / "synthflow-inbound-prompt.md").read_text(encoding="utf-8")
    assert "November 30" not in docs and "Next class start" not in docs and "{{NEXT_ORIENTATION}}" in docs


def test_the_date_in_the_prompt_changes_with_the_day_and_is_not_cached():
    from app.adapters.synthflow import _load_campaign_prompt

    with patch("app.core.orientation.central_today", return_value=date(2026, 11, 11)):
        assert "Thursday, November 19" in _load_campaign_prompt("Cold Lead")
    with patch("app.core.orientation.central_today", return_value=date(2026, 11, 21)):
        p = _load_campaign_prompt("Cold Lead")
    assert "Monday, December 7, 6:30 to 8:00 PM Central" in p and "Thursday, November 19" not in p


def test_the_open_house_text_is_unchanged():
    from app.adapters.synthflow import _load_campaign_prompt

    p = _load_campaign_prompt("New Lead")
    assert "next Open House is on November 19, 2026" in p


def test_the_inbound_push_script_renders_the_date():
    import importlib.util

    spec = importlib.util.spec_from_file_location("inb", ROOT / "execution" / "update_synthflow_inbound_prompt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "{{NEXT_ORIENTATION}}" not in mod.build_prompt()
