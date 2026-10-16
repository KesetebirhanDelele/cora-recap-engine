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
    changes, alerts = [], []

    def set_value(session, key, value, updated_by="x"):
        cfg[key] = value
        changes.append(value)

    monkeypatch.setattr("app.core.app_config.set_config_value", set_value)
    monkeypatch.setattr(G, "_alert", lambda *a, **k: alerts.append(a[2]))
    monkeypatch.setattr(Session, "execute", Session.execute)       # real session; resolve-UPDATE needs the alert table
    with Session(engine) as s:
        s.execute(text("CREATE TABLE alert_events (id TEXT, alert_type TEXT, status TEXT, resolved_at TIMESTAMP)"))

        def load(optouts, delivered, hours_ago=1):
            at = NOW - timedelta(hours=hours_ago)
            for _ in range(optouts):
                s.execute(text("INSERT INTO optout_actions (source, kind, status, created_at) VALUES ('sms_reply','dnd','applied',:t)"), {"t": at})
            for _ in range(delivered):
                s.execute(text("INSERT INTO channel_events (kind, channel, outcome, event_at) VALUES ('delivery','sms','delivered',:t)"), {"t": at})
        yield NS(s=s, cfg=cfg, load=load, changes=changes, alerts=alerts)


def test_rate_counts_only_sms_reply_dnd_and_delivered_texts_in_the_window(env):
    env.load(3, 100)
    env.load(5, 50, hours_ago=30)                                  # outside 24 h
    assert G.rate(env.s, NOW) == (3, 100, 3.0)


def test_pauses_at_two_percent_with_enough_texts_and_alerts_once(env):
    env.load(3, 100)
    assert G.evaluate(env.s, NS(), NOW) == "paused_now"
    assert env.cfg["sms_guard_state"] == "paused" and len(env.alerts) == 1
    assert G.is_paused(env.s, NS())
    assert G.evaluate(env.s, NS(), NOW) == "paused" and len(env.alerts) == 1      # no second alert while paused


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


def test_resumes_when_too_few_texts_remain_to_judge(env):
    env.cfg["sms_guard_state"] = "paused"
    env.load(2, 10)
    assert G.evaluate(env.s, NS(), NOW) == "resumed"


def test_switch_off_disables_the_guard(env):
    env.load(9, 100)
    env.cfg["sms_guard_enabled"] = "false"
    assert G.evaluate(env.s, NS(), NOW) == "off"
    env.cfg["sms_guard_state"] = "paused"
    assert G.is_paused(env.s, NS()) is None
