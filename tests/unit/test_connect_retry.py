"""Connection failures are retried (2026-10-08/09: intermittent [Errno 101] Network is unreachable)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from app.core.http_retry import CONNECT_RETRIES, connect_retry_transport


def test_transport_retries_connection_attempts_only():
    t = connect_retry_transport()
    assert isinstance(t, httpx.HTTPTransport) and CONNECT_RETRIES == 3
    assert t._pool._retries == CONNECT_RETRIES


def test_every_adapter_builds_its_client_with_the_retry_transport():
    from app.adapters.ghl import GHLClient
    from app.adapters.ghl_conversations import GhlConversationsClient
    from app.adapters.ghl_internal_comment import GhlInternalCommentClient
    from app.adapters.synthflow import SynthflowClient

    s = SimpleNamespace(ghl_base_url="https://x.test", ghl_timeout_seconds=5, synthflow_timeout_seconds=5)
    for cls in (GHLClient, GhlConversationsClient, GhlInternalCommentClient, SynthflowClient):
        c = cls(settings=s)
        assert c._http._transport._pool._retries == CONNECT_RETRIES, cls.__name__


def test_smtp_retries_a_connection_failure_then_sends():
    from app.services import alerting

    settings = MagicMock(smtp_enabled=True, smtp_host="h", smtp_port=25, smtp_use_tls=False, smtp_username="", smtp_password="",
                         alert_email_from="a@x.com")
    server = MagicMock()
    calls = {"n": 0}

    def smtp(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(101, "Network is unreachable")
        cm = MagicMock()
        cm.__enter__.return_value = server
        cm.__exit__.return_value = False
        return cm

    with patch.object(alerting.smtplib, "SMTP", side_effect=smtp), patch.object(alerting.time, "sleep"):
        alerting._smtp_send(settings, ["b@x.com"], "s", "body", log_label="t")
    assert calls["n"] == 3 and server.sendmail.call_count == 1
