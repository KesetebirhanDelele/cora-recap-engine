"""Pure tests for the legacy timezone alias map in app/core/campaign_schedule.py (no DB)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from app.core.campaign_schedule import canonical_timezone


@pytest.mark.parametrize("legacy,canonical", [
    ("US/Central", "America/Chicago"),
    ("US/Eastern", "America/New_York"),
    ("US/Mountain", "America/Denver"),
    ("US/Pacific", "America/Los_Angeles"),
    ("US/Alaska", "America/Anchorage"),
    ("US/Hawaii", "Pacific/Honolulu"),
    ("US/Arizona", "America/Phoenix"),
])
def test_legacy_aliases_map_to_a_real_zone(legacy, canonical):
    assert canonical_timezone(legacy) == canonical
    ZoneInfo(canonical)          # the canonical name must itself load


def test_case_and_whitespace_are_tolerated():
    assert canonical_timezone("  us/central ") == "America/Chicago"


@pytest.mark.parametrize("tz", ["America/Chicago", "America/New_York", "Asia/Manila", "UTC", "Not/A_Real_Timezone"])
def test_everything_else_is_returned_unchanged(tz):
    assert canonical_timezone(tz) == tz


def test_aliases_cover_the_same_utc_offset_as_the_old_fallback():
    """US/Central used to fall back to America/Chicago - the mapping must be behaviour-neutral."""
    from datetime import datetime

    jan = datetime(2026, 1, 15, 12, 0)
    jul = datetime(2026, 7, 15, 12, 0)
    for moment in (jan, jul):
        assert ZoneInfo(canonical_timezone("US/Central")).utcoffset(moment) == ZoneInfo("America/Chicago").utcoffset(moment)
