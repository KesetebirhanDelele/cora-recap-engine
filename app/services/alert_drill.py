"""Alert drill: send ONE real-shaped alert email so the recipient can confirm it lands untouched (inbox rule on the
X-Cora-Alert header, no classifier, no spam folder).

It uses the same subject shape, headers, sender and recipients as a real alert, plus `X-Cora-Alert-Drill: true` and a first
line saying no channel is down. It writes NOTHING (no alert_events row, no state change) and never reaches a customer.

    docker compose exec -T worker-default python -m app.services.alert_drill --kind health --channel sms
    docker compose exec -T worker-default python -m app.services.alert_drill --kind system
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from app.core import channel_health as ch

DRILL_LINE = "*** ALERT DRILL - no channel is down and nothing needs doing. This checks that alerts reach you. ***"


def build_drill(kind: str, channel: str = "sms", now: datetime | None = None) -> tuple[str, str, dict[str, str]]:
    """(subject, body, headers) for a drill of a `health` or `system` alert."""
    from app.services.alerting import system_alert_headers
    from app.services.channel_health import alert_headers, alert_subject

    now = now or datetime.now(tz=timezone.utc)
    if kind == "health":
        if channel not in ch.CHANNELS:
            raise ValueError(f"channel must be one of {ch.CHANNELS}")
        subject = alert_subject(channel, "active")
        body = "\n".join([
            DRILL_LINE, "",
            f"{ch.LABEL[channel]} delivery health: ACTIVE (drill)",
            "Why: no delivery in 2.6 days (illustrative)",
            f"Checked: {now.isoformat()}", "",
            "Measured on DELIVERED (GHL/provider status, call logs), per channel. Open the Delivery Health tile for detail.",
        ])
        headers = alert_headers(channel, "active")
    elif kind == "system":
        alert_type = "queue_lag"
        subject = f"[CRITICAL] Cora Alert: {alert_type}"
        body = "\n".join([
            DRILL_LINE, "",
            "Alert ID: drill", f"Type: {alert_type}", "Severity: critical", "Status: active",
            "Message: illustrative queue-lag alert (drill)", f"Time: {now.isoformat()}", "",
            "This is an automated alert from the Cora Recap Engine.",
        ])
        headers = system_alert_headers(alert_type, "critical", "active")
    else:
        raise ValueError("kind must be 'health' or 'system'")
    headers = {**headers, "X-Cora-Alert-Drill": "true"}
    return subject, body, headers


def send_drill(settings: Any, session: Any, kind: str, channel: str = "sms") -> dict[str, Any]:
    """Send the drill to the real recipients of that alert kind (health: to alert_email_to, cc channel_silence_cc)."""
    from app.services.alerting import _smtp_send
    from app.services.channel_health import DEFAULT_CC, _cfg

    subject, body, headers = build_drill(kind, channel)
    to = [x.strip() for x in (getattr(settings, "alert_email_to", "") or "").split(",") if x.strip()]
    cc = ([x.strip() for x in _cfg(session, settings, "channel_silence_cc", DEFAULT_CC).split(",") if x.strip()]
          if kind == "health" else [])
    _smtp_send(settings, to, subject, body, log_label=f"drill:{kind}", cc_addrs=cc, extra_headers=headers)
    return {"subject": subject, "to": to, "cc": cc, "headers": sorted(headers)}


if __name__ == "__main__":
    from app.config import get_settings
    from app.db import get_db

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=("health", "system"), required=True)
    ap.add_argument("--channel", choices=ch.CHANNELS, default="sms")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; send nothing")
    a = ap.parse_args()
    if a.dry_run:
        subj, body, hdr = build_drill(a.kind, a.channel)
        print(subj)
        print(hdr)
        print(body)
    else:
        print(send_drill(get_settings(), next(get_db()), a.kind, a.channel))
