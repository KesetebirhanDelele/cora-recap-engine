"""Who can take a live transfer right now (spec/39). Times use the real production roster and the real call times of 2026-10-23."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core import staff_availability as sa

UTC = timezone.utc
ROSTER = json.dumps([
    {"name": "Balakrishna K", "ghl_id": "b", "days": [0, 1, 2, 3, 4], "shift_start": "04:30", "shift_end": "12:30"},
    {"name": "Farhat Beig", "ghl_id": "f", "days": [0, 1, 2, 3, 4], "shift_start": "14:00", "shift_end": "22:00"},
    {"name": "Balamurali Kasula", "ghl_id": "m", "days": [0, 1, 2, 3, 4], "shift_start": "18:00", "shift_end": "02:00"},
    {"name": "Balamurali Kasula", "ghl_id": "m", "days": [5], "shift_start": "09:00", "shift_end": "01:00"},
])
ADMISSIONS = json.dumps([{"name": "Roselen Flores", "ghl_id": "r"}])
IPBC = json.dumps([{"name": "Taiwo Oludimimu", "ghl_id": "t"}])

FRI_NOON = datetime(2026, 10, 23, 17, 46, tzinfo=UTC)          # Friday 12:46 PM Central: the Suresh call
FRI_NIGHT = datetime(2026, 10, 24, 3, 14, tzinfo=UTC)          # Friday 10:14 PM Central
SAT_NOON = datetime(2026, 10, 24, 17, 0, tzinfo=UTC)           # Saturday 12:00 PM Central
SUN_NOON = datetime(2026, 10, 25, 17, 0, tzinfo=UTC)


def block(now, callback_only=""):
    return sa.availability_block(now, ADMISSIONS, IPBC, ROSTER, callback_only=callback_only)


def line(text, label):
    return next(ln for ln in text.splitlines() if ln.startswith(f"- {label}"))


def test_friday_noon_admissions_and_payments_are_available_support_has_nobody_between_shifts():
    b = block(FRI_NOON)
    assert "Current time: Friday 12:46 PM Central" in b
    assert "AVAILABLE NOW (Roselen)" in line(b, "Admissions")
    assert "AVAILABLE NOW (Taiwo)" in line(b, "Payments")
    assert "NOT available now. Next available: today 2:00 PM Central" in line(b, "Support")      # Balakrishna ended 12:30, Farhat 14:00


def test_friday_night_admissions_and_payments_are_closed_until_monday_but_the_evening_support_shift_is_on():
    b = block(FRI_NIGHT)
    assert "Friday 10:14 PM Central" in b
    assert "NOT available now. Next available: Monday 9:00 AM Central" in line(b, "Admissions")
    assert "NOT available now. Next available: Monday 9:00 AM Central" in line(b, "Payments")
    assert "AVAILABLE NOW (Balamurali)" in line(b, "Support")             # his Friday shift runs 6 PM to 2 AM


def test_nobody_at_all_at_2_30_am_saturday_next_support_shift_is_that_morning():
    b = block(datetime(2026, 10, 24, 7, 30, tzinfo=UTC))                  # Saturday 2:30 AM Central
    assert "NOT available now" in line(b, "Admissions")
    assert "NOT available now. Next available: today 9:00 AM Central" in line(b, "Support")


def test_overnight_shift_keeps_support_available_after_midnight():
    after_midnight = datetime(2026, 10, 24, 5, 30, tzinfo=UTC)          # Saturday 12:30 AM Central, Balamurali's Friday shift runs to 2 AM
    assert "AVAILABLE NOW (Balamurali)" in line(block(after_midnight), "Support")


def test_weekend_admissions_closed_with_next_open_monday():
    b = block(SUN_NOON)
    assert "NOT available now. Next available: Monday 9:00 AM Central" in line(b, "Admissions")


def test_saturday_support_shift_is_available():
    assert "AVAILABLE NOW (Balamurali)" in line(block(SAT_NOON), "Support")


def test_daylight_saving_uses_central_time_not_a_fixed_offset():
    # 2026-12-01 15:00 UTC = 9:00 AM CST (UTC-6): Admissions open. In October (CDT) the same UTC hour is 10 AM.
    assert "AVAILABLE NOW" in line(sa.availability_block(datetime(2026, 12, 1, 15, 0, tzinfo=UTC), ADMISSIONS, IPBC, ROSTER, callback_only=""), "Admissions")
    assert "NOT available now" in line(sa.availability_block(datetime(2026, 12, 1, 14, 59, tzinfo=UTC), ADMISSIONS, IPBC, ROSTER, callback_only=""), "Admissions")


@pytest.mark.parametrize("admissions,ipbc,roster", [(None, None, None), ("", "not json", "[]"), ("[]", "[{}]", '[{"name": "x"}]')])
def test_missing_or_malformed_settings_still_produce_a_usable_block(admissions, ipbc, roster):
    b = sa.availability_block(FRI_NOON, admissions, ipbc, roster, callback_only="")
    assert "AVAILABLE NOW (Roselen)" in line(b, "Admissions") and "AVAILABLE NOW (Taiwo)" in line(b, "Payments")
    assert "schedule unknown" in line(b, "Support")


def test_block_tells_the_agent_not_to_transfer_when_unavailable_and_has_no_banned_word_or_link():
    b = block(FRI_NIGHT)
    assert "NEVER be transferred to" in b and "book it with the booking tool" in b and "keep talking to you" in b
    low = b.lower()
    assert "free" not in low and "http" not in low and ".com" not in low


def test_malformed_roster_entries_are_dropped_not_fatal():
    bad = json.dumps([{"name": "A", "days": [0], "shift_start": "xx", "shift_end": "yy"}, {"nope": 1},
                      {"name": "B", "ghl_id": "b", "days": [0], "shift_start": "09:00", "shift_end": "17:00"}])
    assert [e["name"] for e in sa.parse_roster(bad)] == ["B"]


# -- the adapter puts the block at the very top of the injected prompt ----------------------------------------------
def _launch(note):
    from app.adapters.synthflow import SynthflowClient
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True}, text="")
    settings = MagicMock(synthflow_retry_max=0, synthflow_api_key="k", synthflow_timeout_seconds=5)
    settings.get_synthflow_launch_url.return_value = "https://example.invalid/launch"
    with patch("app.adapters.synthflow._load_campaign_prompt", return_value="BASE PROMPT"):
        SynthflowClient(settings=settings, _http=http).launch_new_lead_call("+15550001", "A", "Cold Lead", availability_note=note)
    return http.post.call_args.kwargs["json"]["prompt"]


def test_prompt_is_the_stored_prompt_when_no_note_is_given():
    assert _launch("") == "BASE PROMPT"


def test_prompt_starts_with_the_live_status_block():
    p = _launch("## LIVE STATUS\n...\n\n")
    assert p.startswith("## LIVE STATUS") and p.endswith("BASE PROMPT")


def test_a_broken_availability_helper_never_stops_a_call():
    from app.worker.jobs import outbound_jobs
    with patch("app.core.app_config.get_str", side_effect=RuntimeError("db down")):
        assert outbound_jobs._availability_note(MagicMock(), MagicMock()) == ""


# -- Admissions and Support are callback-only by default (Synthflow cannot transfer to numbers outside the US) ----------------------------
def test_by_default_admissions_and_support_are_callback_only_and_payments_keeps_live_transfer():
    b = sa.availability_block(FRI_NOON, ADMISSIONS, IPBC, ROSTER)                      # no callback_only argument = the default
    assert "NO live transfer (callback only)" in line(b, "Admissions") and "NO live transfer (callback only)" in line(b, "Support")
    assert "AVAILABLE NOW (Taiwo)" in line(b, "Payments")
    assert "AVAILABLE NOW" not in line(b, "Admissions") and "AVAILABLE NOW" not in line(b, "Support")


def test_callback_only_lines_say_when_the_team_can_call_back_but_never_offer_a_transfer():
    assert "can call back now" in line(sa.availability_block(FRI_NOON, ADMISSIONS, IPBC, ROSTER), "Admissions")
    night = sa.availability_block(FRI_NIGHT, ADMISSIONS, IPBC, ROSTER)
    assert "from Monday 9:00 AM Central" in line(night, "Admissions") and "transfer" not in line(night, "Admissions").replace("NO live transfer", "")


@pytest.mark.parametrize("value,want", [(None, {"admissions", "support"}), ("", set()), ("support", {"support"}), (" Admissions , IPBC ", {"admissions", "ipbc"})])
def test_callback_only_setting_parsing(value, want):
    assert sa.parse_callback_only(value) == frozenset(want)


def test_the_setting_can_turn_a_team_back_into_a_live_transfer_team():
    b = sa.availability_block(FRI_NOON, ADMISSIONS, IPBC, ROSTER, callback_only="support")     # Ali re-enables Admissions transfers
    assert "AVAILABLE NOW (Roselen)" in line(b, "Admissions") and "NO live transfer" in line(b, "Support")


def test_the_block_never_asks_the_caller_to_keep_talking_to_the_agent():
    b = sa.availability_block(FRI_NOON, ADMISSIONS, IPBC, ROSTER)
    assert "Never ask the caller whether they would rather keep talking to you" in b


def test_the_outbound_prompts_never_offer_the_caller_the_choice_to_stay_with_the_agent():
    from app.adapters.synthflow import _load_campaign_prompt
    for c in ("Cold Lead", "New Lead"):
        p = _load_campaign_prompt(c)
        assert "Live transfer is only for Payments / IPBC" in p and 'Never say** "try again later"' in p and "keep talking to me" in p
        assert "NO live transfer: book a callback with Admissions" in p and "NO live transfer: book a callback with Support" in p


# -- Balamurali Kasula's live transfer works, so he is the one exception on the callback-only Support team (Kes 2026-10-26) ---------------------
FRI_EVENING = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)         # Friday 8:00 PM Central: Balamurali (6PM-2AM) and Farhat (2PM-10PM) are on shift


def test_a_named_person_on_a_callback_only_team_can_take_a_live_transfer_while_on_shift():
    b = sa.availability_block(FRI_EVENING, ADMISSIONS, IPBC, ROSTER)                 # defaults: support callback-only, live person = balamurali
    assert "live transfer ONLY to Balamurali" in line(b, "Support") and "callback only" in line(b, "Support")
    assert "Farhat" not in line(b, "Support")                                          # the others on shift stay callback-only
    assert "NO live transfer" in line(b, "Admissions")                                  # Admissions has no exception


def test_when_that_person_is_off_shift_support_is_callback_only_again():
    b = sa.availability_block(FRI_NOON, ADMISSIONS, IPBC, ROSTER)                      # Friday 12:46 PM Central: Balamurali not yet on shift
    assert "NO live transfer (callback only)" in line(b, "Support") and "Balamurali" not in line(b, "Support")


@pytest.mark.parametrize("value,want", [(None, {"balamurali"}), ("", set()), (" Balamurali , Farhat ", {"balamurali", "farhat"})])
def test_live_people_setting_parsing(value, want):
    assert sa.parse_live_people(value) == frozenset(want)


def test_the_live_person_exception_can_be_removed_by_setting_it_empty():
    b = sa.availability_block(FRI_EVENING, ADMISSIONS, IPBC, ROSTER, live_people="")
    assert "live transfer ONLY" not in line(b, "Support")
