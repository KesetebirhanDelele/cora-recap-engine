"""Opt-out guard (app/services/sms_guard.py): pause Cora's texts near GHL's 3% opt-out lockout. Pure + sqlite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services import sms_guard as G

NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE optout_actions (id INTEGER PRIMARY KEY, source TEXT, kind TEXT, status TEXT, created_at TIMESTAMP)"))
        c.execute(text("CREATE TABLE channel_events (id INTEGER PRIMARY KEY, kind TEXT, channel TEXT, outcome TEXT, event_at TIMESTAMP)"))
    cfg = {"sms_guard_state": "ok"}
    monkeypatch.setattr(G, "_cfg", lambda s, st, key, default: cfg.get(key, default))
    changes = []

    def set_value(session, key, value, updated_by="x"):
        cfg[key] = value
        changes.append(value)

    monkeypatch.setattr("app.core.app_config.set_config_value", set_value)
    with Session(engine) as s:
        def load(optouts, delivered, hours_ago=1):
            at = NOW - timedelta(hours=hours_ago)
            for _ in range(optouts):
                s.execute(text("INSERT INTO optout_actions (source, kind, status, created_at) VALUES ('sms_reply','dnd','applied',:t)"), {"t": at})
            for _ in range(delivered):
                s.execute(text("INSERT INTO channel_events (kind, channel, outcome, event_at) VALUES ('delivery','sms','delivered',:t)"), {"t": at})
        yield NS(s=s, cfg=cfg, load=load, changes=changes)


def test_rate_counts_only_sms_reply_dnd_and_delivered_texts_in_the_window(env):
    env.load(3, 100)
    env.load(5, 50, hours_ago=30)                                  # outside 24 h
    assert G.rate(env.s, NOW) == (3, 100, 3.0)


def test_pauses_at_two_percent_with_enough_texts_without_raising_an_alert(env):
    env.load(3, 100)
    assert G.evaluate(env.s, NS(), NOW) == "paused_now"
    assert env.cfg["sms_guard_state"] == "paused" and G.is_paused(env.s, NS())
    assert not hasattr(G, "_alert")                                # Kes 2026-10-16: no alert or email for a pause / resume
    assert G.evaluate(env.s, NS(), NOW) == "paused" and env.changes == ["paused"]


def test_small_samples_never_pause(env):
    env.load(2, 20)                                                # 10% but only 20 texts
    assert G.evaluate(env.s, NS(), NOW) == "ok" and G.is_paused(env.s, NS()) is None


def test_below_the_line_stays_running(env):
    env.load(1, 100)                                               # 1%
    assert G.evaluate(env.s, NS(), NOW) == "ok"


def test_resumes_only_below_the_lower_line(env):
    env.cfg["sms_guard_state"] = "paused"
    env.load(2, 100)                                               # 2% - still above 1.5%
    assert G.evaluate(env.s, NS(), NOW) == "paused"
    env.load(0, 100)                                               # now 2 / 200 = 1%
    assert G.evaluate(env.s, NS(), NOW) == "resumed" and env.cfg["sms_guard_state"] == "ok"


def test_resumes_when_too_few_texts_remain_to_judge_and_opt_outs_are_low(env):
    env.cfg["sms_guard_state"] = "paused"
    env.load(1, 10)
    assert G.evaluate(env.s, NS(), NOW) == "resumed"


def test_a_small_sample_with_several_opt_outs_stays_paused(env):
    """2026-10-16: a GHL lockout shrank the delivered count to 44; 2 opt-outs (4.5%) must not read as 'too few to judge'."""
    env.cfg["sms_guard_state"] = "paused"
    env.load(2, 44)
    assert G.evaluate(env.s, NS(), NOW) == "paused" and G.is_paused(env.s, NS())


def test_switch_off_disables_the_guard(env):
    env.load(9, 100)
    env.cfg["sms_guard_enabled"] = "false"
    assert G.evaluate(env.s, NS(), NOW) == "off"
    env.cfg["sms_guard_state"] = "paused"
    assert G.is_paused(env.s, NS()) is None


# ── dashboard status (Delivery Health banner) ─────────────────────────────────

def _app_config(env, updated_at):
    env.s.execute(text("CREATE TABLE app_config (key TEXT, updated_at TIMESTAMP)"))
    env.s.execute(text("INSERT INTO app_config (key, updated_at) VALUES ('sms_guard_state', :t)"), {"t": updated_at})


def test_status_explains_a_pause_with_numbers_and_when_it_can_end(env):
    env.load(3, 42, hours_ago=2)                                   # 3 opt-outs on 42 texts = 7.1 %
    env.cfg["sms_guard_state"] = "paused"
    _app_config(env, NOW - timedelta(hours=1))
    st = G.status(env.s, NS(), NOW)
    assert st["paused"] is True and st["optouts"] == 3 and st["delivered"] == 42 and st["pct"] == 7.1
    assert "paused" in st["reason"] and "7.1%" in st["reason"] and "2%" in st["reason"]
    assert "1.5%" in st["resumes_when"] and "50" in st["resumes_when"]
    assert st["paused_since"] == (NOW - timedelta(hours=1)).isoformat()
    assert st["next_optout_leaves_at"] == (NOW - timedelta(hours=2) + timedelta(hours=24)).isoformat() + "+00:00" \
        or st["next_optout_leaves_at"].startswith((NOW - timedelta(hours=2) + timedelta(hours=24)).isoformat()[:19])


def test_status_when_running_has_no_pause_reason(env):
    env.load(0, 80)
    _app_config(env, NOW)
    st = G.status(env.s, NS(), NOW)
    assert st["paused"] is False and st["reason"] is None and st["paused_since"] is None and st["next_optout_leaves_at"] is None


def test_status_off_when_the_guard_is_disabled(env):
    env.cfg["sms_guard_enabled"] = "false"
    env.cfg["sms_guard_state"] = "paused"
    _app_config(env, NOW)
    assert G.status(env.s, NS(), NOW)["paused"] is False


def test_status_never_raises_if_it_cannot_read_the_state(env):
    st = G.status(env.s, NS(), NOW)                                # no app_config table in this fixture
    assert st["paused"] is False and "error" in st
