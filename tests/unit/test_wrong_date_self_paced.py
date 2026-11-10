"""Self-paced enrollment (Ali 2026-11-10): no class start date, orientation dates are legitimate, and the correction email says so."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.core.wrong_date_guard import find_wrong_dates
from app.services import wrong_date_monitor as wdm

OH = "November 19, 2026"
NO_CLASS = ""                                  # the dashboard setting is blank now


def flagged(body, cls=NO_CLASS, oh=OH):
    return find_wrong_dates(body, None, expected_class_start=cls, expected_open_house=oh)


# ── the detector ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_orientation_date_is_never_flagged_even_with_no_class_start_set():
    assert flagged("<p>The next orientation is Thursday, November 12, and the one after that is Monday, December 7.</p>") == []
    assert flagged("Your orientation is on Monday, January 4, 6:30 to 8:00 PM Central.") == []


def test_a_class_start_date_is_still_flagged_when_none_is_scheduled():
    assert flagged("Our next class starts on November 30, 2026.") != []
    assert flagged("Join the cohort that begins November 30.") != []


def test_the_open_house_date_is_still_checked():
    assert flagged("Join our Open House on November 19, 2026.") == []
    assert flagged("Join our Open House on November 30, 2026.") != []


# ── the correction email ────────────────────────────────────────────────────────────────────────────────────────────────────
def html(today=date(2026, 11, 11)):
    return wdm._self_paced_html(OH, "https://www.eventbrite.com/e/x-123", "training.colaberry.com/start-free",
                                "https://training.colaberry.com/start-free", "Cora from Colaberry", today=today)


def test_the_correction_says_there_is_no_class_start_and_gives_the_orientation_and_open_house():
    h = html()
    for must in ("incorrect date", "There is no class start date", "when you enroll, you start right away",
                 "first Monday of the month after you join, 6:30 to 8:00 PM Central",
                 "The next orientation is <strong>Thursday, November 12</strong>",
                 "The one after that is <strong>Monday, December 7, 6:30 to 8:00 PM Central</strong>",
                 "Next Open House: <strong>November 19, 2026</strong>", "RSVP here", "https://www.eventbrite.com/e/x-123",
                 "start learning for $0", "Cora from Colaberry"):
        assert must in h, must
    assert "November 30" not in h


def test_the_orientation_rolls_forward_after_nov_12():
    h = html(today=date(2026, 11, 13))
    assert "The next orientation is <strong>Monday, December 7, 6:30 to 8:00 PM Central</strong>" in h
    assert "The one after that is <strong>Monday, January 4, 6:30 to 8:00 PM Central</strong>" in h


def test_the_correction_email_is_not_itself_flagged_as_a_wrong_date():
    assert flagged(html()) == []


def test_without_an_open_house_there_is_no_open_house_line():
    h = wdm._self_paced_html("", None, "u", "https://u", "Cora", today=date(2026, 11, 11))
    assert "Open House" not in h and "no class start date" in h


def test_the_subject_follows_the_model_and_an_explicit_setting_still_wins():
    def subj(class_start, override=None):
        cfg = {"correction_email_subject": override} if override else {}
        with patch.object(wdm, "expected_dates", lambda s, st: (class_start, OH)), \
                patch.object(wdm, "_config", lambda s, st, key, default=None: cfg.get(key, default)):
            return wdm.correction_email_subject(None, SimpleNamespace())

    assert subj("") == "Correction: when you start and your orientation date"
    assert subj("November 12, 2026") == "Correction: our class and Open House dates"      # the old model is unchanged
    assert subj("", override="Custom subject") == "Custom subject"


def test_build_correction_email_uses_the_self_paced_text_when_no_class_start_is_set():
    vals = {"live_open_house_link": "https://www.eventbrite.com/e/x-123", "sender_name": "Cora from Colaberry"}
    with patch.object(wdm, "expected_dates", lambda s, st: ("", OH)), \
            patch.object(wdm, "_config", lambda s, st, key, default=None: vals.get(key, default)):
        subject, body = wdm.build_correction_email(None, SimpleNamespace())
    assert subject == "Correction: when you start and your orientation date"
    assert "There is no class start date" in body and "Next Open House: <strong>November 19, 2026</strong>" in body
    with patch.object(wdm, "expected_dates", lambda s, st: ("November 12, 2026", OH)), \
            patch.object(wdm, "_config", lambda s, st, key, default=None: vals.get(key, default)):
        _, old_body = wdm.build_correction_email(None, SimpleNamespace())
    assert "Next class starts: <strong>November 12, 2026</strong>" in old_body and "no class start date" not in old_body
