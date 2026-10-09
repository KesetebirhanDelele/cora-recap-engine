"""Did a lead ask us to contact them? (pure, no I/O; Kes 2026-10-09)

Used for leads who are on DND or carry an opt-out tag: Cora does not override DND, but a person must know the lead asked, so
they can confirm it, switch DND off in GHL and clear Cora's do-not-call flag. Precision over recall; quoted text and our own
footers are ignored, and negations ("don't call me") are not requests.
"""
from __future__ import annotations

import re

from app.core import optout as oo

MAX_CHARS = 700
_NEG = re.compile(r"(?i)\b(?:don'?t|do not|never|stop|no)\b[^.!?\n]{0,25}\b(?:call|text|email|contact|message)\b")
_ASK = re.compile(
    r"(?i)\b(?:"
    r"(?:please\s+|pls\s+|can you\s+|could you\s+|would you\s+|can someone\s+)?(?:call|phone|ring)\s+me(?:\s+back)?|"
    r"give\s+me\s+a\s+(?:call|ring)|"
    r"(?:please\s+)?(?:text|email|e-mail)\s+me\b|"
    r"(?:please\s+)?send\s+me\s+(?:more\s+|some\s+)?(?:info|information|details|the\s+details|a\s+link|the\s+link|the\s+brochure)|"
    r"(?:i(?:'d| would)\s+like|i\s+want|i\s+need|looking\s+for)\s+(?:more\s+|some\s+)?(?:info|information|details)|"
    r"(?:tell|give)\s+me\s+(?:more\s+)?(?:about|info|information|details)|"
    r"(?:i(?:'m| am)\s+)?(?:interested|ready\s+to\s+(?:enroll|start|sign))|"
    r"call\s+(?:me\s+)?(?:back|tomorrow|today|later|at\s+\d)"
    r")\b")


def asks_to_be_contacted(body: str | None, channel: str = "sms") -> bool:
    """True when what the lead wrote this time is a request to be called, texted or sent information."""
    text = oo.clean_reply(body, channel)
    if not text:
        return False
    if oo.classify(text, "sms").kind == oo.DND:         # a stop request wins over any other words in the same reply
        return False
    if _NEG.search(text):
        return False
    return bool(_ASK.search(text))
