"""What a lead actually wrote in an email reply (2026-11-17: a "Stop" reply was read as interest in the quoted Open House invite)."""
from __future__ import annotations

from app.core import email_reply as er

QUOTED = ("On Nov 17, 2026, at 12:25 PM, Colaberry Admissions Team &lt;admissions@colaberry.com&gt; wrote:\n"
          "Hello Future AI Architect,\nJoin us for the Colaberry AI Systems Architect Accelerator Open House.\nNovember 19, 2026")
REAL = "Stop\nBest regards,\nElijah Choy\n" + QUOTED


def test_the_real_stop_reply_is_cut_before_our_quoted_invite():
    out = er.reply_text(REAL)
    assert out.startswith("Stop") and "Open House" not in out and "Hello Future" not in out


def test_the_real_stop_reply_is_an_opt_out_even_with_a_sign_off():
    assert er.is_stop_request(REAL) is True


def test_html_bodies_and_entities_are_flattened():
    assert er.reply_text("<div>Please call me</div><div>On Mon, Nov 9, 2026 at 1:00 PM X &lt;x@y.com&gt; wrote:</div><div>old</div>") == "Please call me"


def test_outlook_style_markers_and_quote_arrows_are_cut():
    assert er.reply_text("Yes please\n-----Original Message-----\nFrom: us\nold") == "Yes please"
    assert er.reply_text("Sounds good\n> earlier text\n> more") == "Sounds good"


def test_a_message_that_is_only_a_quote_is_not_reduced_to_nothing():
    assert "wrote:" in er.reply_text("On Mon, Nov 9, 2026 at 1:00 PM X wrote:\nhello")


def test_a_real_question_is_not_a_stop_request():
    assert er.is_stop_request("Please don't stop calling me, I want to enroll\n" + QUOTED) is False
    assert er.is_stop_request("Can someone call me about the program?") is False
    assert er.is_stop_request("") is False and er.is_stop_request(None) is False


def test_other_bare_stop_words_count():
    for w in ("STOP", "Unsubscribe", "stop all", "opt out"):
        assert er.is_stop_request(w + "\n" + QUOTED) is True
