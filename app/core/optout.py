"""
Opt-out wording classifier - pure, no I/O (spec/36).

Decides, from what a lead SAID (call transcript lines, SMS reply, email reply), whether they asked us to stop and
on WHICH channels, so GHL's real DND can be applied by the lead's own wording:

  "stop calling me"            -> {call}
  "stop texting" / "no more texts" -> {sms}
  "stop emailing me"           -> {email}
  "stop messaging me"          -> {sms, email}
  "remove me" / "unsubscribe" / "leave me alone" / "do not contact" -> {call, sms, email}
  bare "STOP" on an SMS -> {sms}; bare "unsubscribe" on an email -> {email}

Precision over recall: a clear phrase is HIGH confidence (auto-applied); anything that merely smells like an opt-out
is UNCLEAR (goes to the LLM, then to the review list). Quoted text and our own footers are removed first, because
our emails contain the word "unsubscribe" and a reply that quotes them is not an opt-out.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CALL, SMS, EMAIL = "call", "sms", "email"
ALL = frozenset({CALL, SMS, EMAIL})
DND, NOT_INTERESTED, WRONG_NUMBER, UNCLEAR, NONE = "dnd", "not_interested", "wrong_number", "unclear", "none"
HIGH, MEDIUM = "high", "medium"

MAX_CHARS = 700            # an opt-out is near the top of a reply; signatures further down are noise
EMAIL_HEAD_CHARS = 350     # ... and in an email only the first lines count: "unsubscribe" far down is a footer

_QUOTE_CUT = re.compile(
    r"(?im)^(?:on .{5,120}wrote:|-{2,}\s*original message\s*-{2,}|_{5,}|from:\s.+|sent from my .+)$")
_QUOTE_LINE = re.compile(r"(?m)^\s*>.*$")
_AUTO = re.compile(r"(?i)out of office|automatic reply|auto[- ]?reply|autoreply|undeliverable|delivery status notification|"
                   r"mailer-daemon|vacation (reply|responder)|away from (my )?(desk|email)")
_NEG_STOP = re.compile(r"(?i)\b(?:don'?t|do not|not|never)\s+stop\b|\bstop\s+by\b|\bstop\s+(?:in|over)\b")
# A stop request needs the verb to sit RIGHT next to its object ("stop calling", "don't text me", "no more emails",
# "don't send me any more emails"). Loose "verb ... within 4 words ... object" matching misfired on
# "I never received the email" and "don't forget to email me" (spec/36, found scanning production transcripts).
_STOP = r"(?:stop|quit|cease|no\s+more)"
_DONT = r"(?:(?:please\s+)?(?:do\s*not|don'?t|dont)(?:\s+ever)?|never|(?:asked|told)\s+you\s+not\s+to)"
_LEAD = rf"(?:{_STOP}|{_DONT})"
_FILL = r"(?:\s+(?:send(?:ing)?|giv(?:e|ing)|leav(?:e|ing))\s+me)?(?:\s+(?:all|any|more|further|these|those|the|your|my|me))*"


def _rx(obj: str) -> re.Pattern:
    return re.compile(rf"(?i)\b{_LEAD}{_FILL}\s+{obj}\b(?!\s+(?:back|you\b))")


_CALL_RE = _rx(r"(?:calls?|calling|phon(?:e|es|ing)|ring(?:ing)?|dial(?:ing)?)")
_SMS_RE = _rx(r"(?:texts?|texting|sms)")
_EMAIL_RE = _rx(r"(?:e-?mails?|emailing|mailing|mail)")
_MSG_RE = _rx(r"(?:messag\w+)")
_ALL_RE = [re.compile(p, re.I) for p in (
    r"\bunsubscribe\b", r"\bopt[\s-]?out\b", r"\bremove\s+(?:me|my\s+(?:number|name|email|info|information|phone))\b",
    r"\btake\s+(?:me|my\s+(?:number|name|email|info|information|phone))\s+off\b",
    r"\bleave\s+me\s+alone\b", r"\bdelete\s+my\s+(?:number|info|information|data|contact)\b", r"\bdo\s+not\s+contact\b",
    r"\bdon'?t\s+contact\b", r"\bnever\s+contact\b", r"\b(?:stop|quit)\s+(?:contacting|communicating|reaching\s+out)\b",
    r"\bstop\s+(?:all\s+)?(?:communications?|contact)\b", r"\bblock\s+(?:me|this\s+number)\b",
    r"\b(?:stop|quit)\s+(?:bothering|harassing|spamming)\b")]
_KEYWORD = re.compile(r"(?i)^\W*(stop|stopall|stop all|unsubscribe|cancel|end|quit|opt[\s-]?out|remove|remove me|"
                      r"unsub|do not contact)\W*$")
_NOT_INT = re.compile(r"(?i)\bnot\s+interested\b|\bno\s+thanks?\b|\bno\s+thank\s+you\b|\bnot\s+looking\b|\bnot\s+for\s+me\b")
_WRONG = re.compile(r"(?i)\bwrong\s+(?:number|person)\b|\bno\s+one\s+(?:here\s+)?by\s+that\s+name\b|\bnot\s+(?:\w+\s+)?my\s+number\b")
_HINT = re.compile(r"(?i)\b(stop|remove|unsubscribe|leave\s+me|harass\w*|spam\w*|report(?:ed|ing)?|sue|lawyer|attorney|tcpa|"
                   r"no\s+more|quit|annoying|bother\w*|block|fcc|had\s+enough|enough\s+already|that'?s\s+enough|"
                   r"do\s+not\s+(?:want|need)|don'?t\s+(?:want|need)\s+(?:any|this|your|more|to\s+be))\b")


@dataclass(frozen=True)
class OptOut:
    kind: str = NONE                      # dnd | not_interested | wrong_number | unclear | none
    scope: frozenset = frozenset()        # channels to DND (kind == dnd)
    confidence: str = MEDIUM
    phrase: str = ""                      # what matched (for the audit trail / review list)


_OBJS = r"(calls?|calling|phon\w+|texts?|texting|sms|e-?mails?|emailing|mail\w*|messag\w+)\b"
_CONJ_FIRST = re.compile(r"(?i)^\s*(?:,\s*)?(?:or|and|&|/)\s*(?:the\s+)?" + _OBJS)      # "don't call OR text me"
_CONJ_NEXT = re.compile(r"(?i)^\s*(?:,\s*)?(?:(?:or|and|&|/)\s*)?(?:the\s+)?" + _OBJS)  # "..., text, or email"


def _conjunctions(text: str, end: int) -> set[str]:
    """Channels named right after an opt-out phrase: 'don't call or text me' - the 'text' after 'call or'.
    The first extra channel needs an explicit or/and ("don't text, call" means: text no, call yes)."""
    out: set[str] = set()
    for i in range(3):
        m = (_CONJ_FIRST if i == 0 else _CONJ_NEXT).match(text[end:])
        if not m:
            break
        w = m.group(1).lower()
        out |= ({CALL} if w.startswith(("call", "phon")) else {SMS} if w.startswith(("text", "sms"))
                else {EMAIL} if "mail" in w else {SMS, EMAIL})
        end += m.end()
    return out


# Cora's own wording and the platform's email footer. GHL logs some of OUR outgoing emails as inbound messages, and a
# real reply may quote us without ">" markers. Both contain the word "unsubscribe" ("...If you no longer wish to
# receive these emails you may unsubscribe [link]") - that is NOT the lead asking to stop (spec/36, found live:
# 5 DNDs were wrongly applied to leads who had said nothing).
_OWN = re.compile(r"(?i)this is cora from|it'?s cora from|cora from colaberry|text stop to stop alerts|"
                  r"if you no longer wish to receive these emails|unsubscribe\s*[\[<(]?\s*https?://|"
                  r"services\.msgsndr\.com/emails|"
                  # other senders' boilerplate that tells the reader HOW to stop (payment notices, reminders, newsletters)
                  r"reply\s+stop\s+to\b|msg\s*(?:&|and)?\s*data\s+rates|to\s+unsubscribe\b|click\s+(?:here\s+)?to\s+unsubscribe|"
                  r"manage\s+(?:your\s+)?(?:email\s+)?(?:preferences|subscriptions?)")
_PERSONAL = re.compile(r"(?i)\b(i|i'm|i've|i'll|me|my|we|please|thanks?|thank|call|stop|remove|yes|no|okay|ok|sure|"
                       r"interested|sorry)\b")


def split_own(text: str | None) -> tuple[str, bool]:
    """(what comes before Cora's own wording, whether such wording was found)."""
    t = text or ""
    m = _OWN.search(t)
    return (t[: m.start()], True) if m else (t, False)


def is_own_echo(text: str | None) -> bool:
    """True when the 'reply' is just Cora's own email / footer logged back (nothing personal written before it)."""
    lead, found = split_own(text)
    return found and not _PERSONAL.search(lead)


def clean_reply(text: str | None, channel: str) -> str:
    """Only what the lead wrote this time: no quoted history, no signatures / footers / Cora's own text, capped."""
    t = (text or "").replace("\r", "")
    if channel == EMAIL:
        t = _QUOTE_LINE.sub("", t)
        m = _QUOTE_CUT.search(t)
        if m:
            t = t[: m.start()]
    t, _ = split_own(t)
    return re.sub(r"\s+", " ", t).strip()[: EMAIL_HEAD_CHARS if channel == EMAIL else MAX_CHARS]


def is_auto_reply(text: str | None) -> bool:
    return bool(text and _AUTO.search(text[:1500]))


def classify(text: str | None, channel: str) -> OptOut:
    """`channel` is where the words were said: call | sms | email."""
    t = clean_reply(text, channel)
    if not t or (channel == EMAIL and is_auto_reply(text)):
        return OptOut()
    km = _KEYWORD.match(t)
    if km:
        # a bare STOP / UNSUBSCRIBE applies to the channel it came on; "do not contact"/"remove me" to all
        word = km.group(1).lower()
        scope = ALL if word in ("remove", "remove me", "do not contact") and channel == CALL else (
            frozenset({channel}) if channel in ALL else ALL)
        return OptOut(DND, scope, HIGH, km.group(0).strip())

    probe = _NEG_STOP.sub(" ", t)
    scope: set[str] = set()
    phrase = ""
    for rx, channels in ((_CALL_RE, {CALL}), (_SMS_RE, {SMS}), (_EMAIL_RE, {EMAIL}), (_MSG_RE, {SMS, EMAIL})):
        m = rx.search(probe)
        if m:
            scope |= channels
            phrase = phrase or m.group(0)
            scope |= _conjunctions(probe, m.end())        # "don't call or text me" -> call + sms
    for rx in _ALL_RE:
        m = rx.search(probe)
        if m:
            scope |= ALL
            phrase = phrase or m.group(0)
    if scope:
        return OptOut(DND, frozenset(scope), HIGH, phrase.strip())
    if _WRONG.search(t):
        return OptOut(WRONG_NUMBER, frozenset(), HIGH, _WRONG.search(t).group(0))
    if _NOT_INT.search(t):
        # "not interested" is a campaign close (AI Campaign = No), not a legal DND - but a hint word makes it unclear
        if _HINT.search(probe):
            return OptOut(UNCLEAR, frozenset(), MEDIUM, _HINT.search(probe).group(0))
        return OptOut(NOT_INTERESTED, frozenset(), HIGH, _NOT_INT.search(t).group(0))
    h = _HINT.search(probe)
    if h:
        return OptOut(UNCLEAR, frozenset(), MEDIUM, h.group(0))
    return OptOut()


def human_lines(transcript: str | None) -> str:
    """Only what the LEAD said in a Synthflow transcript ('human: ...' lines; 'bot: ...' is our agent)."""
    out = []
    for line in (transcript or "").splitlines():
        head, _, rest = line.partition(":")
        if head.strip().lower() in ("human", "user", "customer", "lead", "caller") and rest.strip():
            out.append(rest.strip())
    return "\n".join(out)


def classify_call(transcript: str | None) -> OptOut:
    """Opt-out wording in the lead's own lines of a call. Each line is judged on its own (so a quoted
    sentence cannot join two); scopes are unioned."""
    best = OptOut()
    scope: set[str] = set()
    phrase = ""
    for line in human_lines(transcript).splitlines():
        r = classify(line, CALL)
        if r.kind == DND:
            scope |= set(r.scope)
            phrase = phrase or r.phrase
        elif r.kind in (WRONG_NUMBER, NOT_INTERESTED, UNCLEAR) and best.kind in (NONE, UNCLEAR):
            best = r
    if scope:
        # on a call, a bare "stop"/"remove me" means calls at least; explicit scopes (text/email) are kept as said
        return OptOut(DND, frozenset(scope), HIGH, phrase)
    return best
