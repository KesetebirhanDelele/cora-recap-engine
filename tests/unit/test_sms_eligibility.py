"""Unit tests for app/core/sms_eligibility.py - pure logic, no I/O (spec/32)."""
from __future__ import annotations

import pytest

from app.core.sms_eligibility import (
    BLOCK,
    HOLD,
    cora_state_verdict,
    ghl_dnd_reason,
    in_tcpa_hours,
    is_stop_reply,
)


# ── GHL DND ──────────────────────────────────────────────────────────────────

def test_clean_contact_has_no_dnd():
    assert ghl_dnd_reason({"dnd": False, "dndSettings": {}, "tags": ["new lead"]}) is None


@pytest.mark.parametrize("c", [None, {}])
def test_missing_contact_has_no_dnd_signal(c):
    assert ghl_dnd_reason(c) is None


def test_global_dnd_blocks():
    assert "all channels" in ghl_dnd_reason({"dnd": True})


@pytest.mark.parametrize("status", ["active", "permanent", "ACTIVE"])
def test_sms_dnd_blocks(status):
    assert "SMS" in ghl_dnd_reason({"dndSettings": {"SMS": {"status": status}}})


def test_email_only_dnd_does_not_block_sms():
    c = {"dnd": False, "dndSettings": {"Email": {"status": "active"}, "SMS": {"status": "inactive"}}}
    assert ghl_dnd_reason(c) is None


def test_call_only_dnd_does_not_block_sms():
    assert ghl_dnd_reason({"dndSettings": {"Call": {"status": "active"}}}) is None


@pytest.mark.parametrize("tag", ["DND", "Unsubscribed", "do not text", "STOP", " opted out "])
def test_opt_out_tags_block(tag):
    assert "tagged" in ghl_dnd_reason({"tags": ["warm lead", tag]})


def test_unrelated_tag_containing_stop_does_not_block():
    assert ghl_dnd_reason({"tags": ["stop-on-response workflow"]}) is None


# ── STOP replies ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", ["STOP", "stop", " Stop. ", "UNSUBSCRIBE", "stop all", "Opt out", "QUIT!"])
def test_stop_replies_detected(body):
    assert is_stop_reply(body)


@pytest.mark.parametrize("body", ["please don't stop calling", "Stop by tomorrow?", "yes", "", None])
def test_normal_replies_not_stop(body):
    assert not is_stop_reply(body)


# ── Cora state ───────────────────────────────────────────────────────────────

def test_no_lead_row_and_no_stop_is_fine():
    assert cora_state_verdict(None, False) is None


def test_stop_reply_blocks_even_without_lead_row():
    assert cora_state_verdict(None, True).action == BLOCK


@pytest.mark.parametrize("lead", [
    {"do_not_call": True},
    {"invalid": True},
    {"status": "closed"},
    {"sales_outcome": "not_interested"},
    {"sales_outcome": "wrong_number"},
])
def test_cora_flags_block(lead):
    assert cora_state_verdict(lead, False).action == BLOCK


def test_replied_lead_is_held_not_blocked():
    v = cora_state_verdict({"last_replied_at": "2026-09-30"}, False)
    assert v.action == HOLD and v.kind == "replied"


def test_block_beats_hold():
    assert cora_state_verdict({"do_not_call": True, "last_replied_at": "x"}, False).action == BLOCK


def test_active_lead_is_fine():
    assert cora_state_verdict({"do_not_call": False, "status": "active", "campaign_name": "New Lead"}, False) is None


# ── TCPA hours ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,ok", [(7, False), (8, True), (14, True), (20, True), (21, False), (23, False), (0, False)])
def test_tcpa_hours(hour, ok):
    assert in_tcpa_hours(hour) is ok
