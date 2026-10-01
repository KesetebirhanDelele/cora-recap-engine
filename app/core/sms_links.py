"""
SMS link policy - pure, no I/O (spec/33).

Decision (Kes, 2026-10-01): NO link of any kind goes out in an SMS - not even Colaberry's own
myfreeaiclass.com. A follow-up written as an SMS that carries any link is delivered by EMAIL instead.

The allow-list is therefore EMPTY by default. app_config `sms_allowed_link_domains` (comma-separated)
can re-allow specific domains later (for example "myfreeaiclass.com" once the A2P registration is
confirmed to allow embedded links) without a deploy.
Email addresses are not links. Detection covers http(s):// URLs, www. hosts, and bare domains on
common TLDs (so "colaberry.com" and "bit.ly/x" are caught).
"""
from __future__ import annotations

import html
import re

DEFAULT_ALLOWED_SMS_DOMAINS = ""      # no links in SMS at all

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TLDS = "com|net|org|edu|gov|io|co|ai|us|ly|me|app|dev|info|biz|tv|link|xyz|site|online|page|school|training"
_LINK = re.compile(
    rf"(?i)\b(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b(?:/[^\s<>\"']*)?"
)
_TRAILING = ".,;:!?)]}\"'"
_STOP_LINE = re.compile(r"(?is)\s*text\s+stop\b[^.!?\n]*[.!?]?\s*$")


def parse_allowed_domains(value: str | None) -> frozenset[str]:
    return frozenset(
        d.strip().lower().removeprefix("www.") for d in (value if value is not None else DEFAULT_ALLOWED_SMS_DOMAINS).split(",")
        if d.strip()
    )


def extract_links(text: str | None) -> list[str]:
    """Every link-looking token in the text (email addresses excluded), trailing punctuation trimmed."""
    cleaned = _EMAIL.sub(" ", text or "")
    return [m.group(0).rstrip(_TRAILING) for m in _LINK.finditer(cleaned)]


def _host(link: str) -> str:
    h = re.sub(r"(?i)^https?://", "", link).split("/")[0].split("?")[0].split("#")[0].lower()
    return h.removeprefix("www.")


def disallowed_links(text: str | None, allowed: frozenset[str]) -> list[str]:
    """Links in `text` whose host is not an allowed domain (or a subdomain of one)."""
    out = []
    for link in extract_links(text):
        host = _host(link)
        if not any(host == d or host.endswith("." + d) for d in allowed):
            out.append(link)
    return out


def sms_needs_email(text: str | None, allowed: frozenset[str]) -> bool:
    """True when the text carries a link that is not allowed in an SMS -> deliver by email instead."""
    return bool(disallowed_links(text, allowed))


# ── turning a link-bearing text into an email ───────────────────────────────

def email_subject_from_text(text: str, fallback: str = "A quick note from Colaberry", limit: int = 70) -> str:
    """First sentence of the text, link-free, as a short subject (varies per message, so the GHL
    'Ticket #2 has changed' trigger fires)."""
    body = _STOP_LINE.sub("", text or "").strip()
    clean = _LINK.sub("", _EMAIL.sub("", body))
    sentences = [x.strip(" \t-:;,") for x in re.split(r"(?<=[.!?])\s+", clean) if x.strip(" \t-:;,")]
    # first sentence with enough words to mean something ("Hi there!" alone is a useless subject)
    first = next((x for x in sentences if len(x.split()) >= 4), sentences[0] if sentences else "")
    if not first:
        return fallback
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def email_html_from_text(text: str) -> str:
    """Plain SMS text -> small HTML email body: paragraphs, URLs clickable, STOP line dropped
    (GHL appends the unsubscribe footer)."""
    body = _STOP_LINE.sub("", text or "").strip()
    out = []
    for para in [p.strip() for p in re.split(r"\n\s*\n|\n", body) if p.strip()]:
        pieces, last = [], 0
        for m in _LINK.finditer(para):
            pieces.append(html.escape(para[last:m.start()]))
            raw = m.group(0).rstrip(_TRAILING)
            href = raw if raw.lower().startswith("http") else f"https://{raw}"
            pieces.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(raw)}</a>')
            last = m.start() + len(raw)
        pieces.append(html.escape(para[last:]))
        out.append("<p>" + "".join(pieces) + "</p>")
    return "".join(out)
