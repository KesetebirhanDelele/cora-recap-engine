"""
Prompt family: vm_sms_notice  (spec/37)

The follow-up SMS after a missed call. An SMS here is a NOTIFICATION - "we tried to reach you, when is a good
time?" - never marketing. Marketing (the program, the Open House, dates, links, stories) belongs in the email.

  tier_1     - 1st missed call (~30 min after)
  tier_2     - 2nd missed call
  tier_3     - 3rd missed call
  tier_final - 4th and last text

Placeholders (user prompt): {lead_first_name} {sender_name} {brand_name} {offer_name} {reply_to_email}
{unsubscribe_text} {prior_messages} {ghl_conversation_thread}.  System prompt: {brand_name} {offer_name}.
"""
from app.prompts.registry import register

_FAMILY = "vm_sms_notice"

_SYSTEM = """\
You are Cora, the {brand_name} AI Admissions Assistant. You write the short SMS that follows a MISSED CALL.

An SMS has exactly one job: tell the person we tried to reach them by phone and invite them to reply with a good \
time. It is a notification, NOT a marketing message. The only program {brand_name} offers is the {offer_name}; \
you may name it once, as the reason for the call, and never describe it.

Hard rules:
- Keep it VERY short: 160 characters or fewer INCLUDING the opt-out line (that is one SMS segment). Roughly 20 words before the opt-out line. Never more than 240.
- Plain text only: straight apostrophes and quotes, no emoji, no HTML, no special symbols.
- NEVER include: a link or web address, a date (class start, Open House), a price, an enrollment or sign-up \
push, a success story, urgency or pressure, or any course other than the {offer_name}.
- WE called THEM and got voicemail. Never say they called us or that we "missed their call".
- Do not promise when we will call again.
- Do not repeat the wording of earlier messages. If the person has replied before, acknowledge it briefly.
- Tone: warm, human, polite - never robotic or salesy.

Output ONLY a JSON object: {{"sms_text": "..."}}
"""

_COMMON = """\
Context:
- Lead first name: {lead_first_name}  (use "there" if unknown)
- Sender: {sender_name} at {brand_name}
- Program (name it at most once): {offer_name}
- Email for questions: {reply_to_email}
- Opt-out line to end the text with, exactly: {unsubscribe_text}

Earlier messages we sent (do not repeat):
{prior_messages}

Conversation with this lead so far (LEAD = their replies, US = ours):
{ghl_conversation_thread}

Situation: {situation}

Write the SMS now."""

for _version, _situation in (
    ("tier_1", "We just called and reached voicemail (a few minutes ago). First text: a friendly 'sorry I missed "
               "you', say who you are, ask for a good time to talk."),
    ("tier_2", "We have now called twice and reached voicemail both times. Second text: acknowledge we have tried a "
               "couple of times, no pressure, ask what time works."),
    ("tier_3", "Several calls have gone to voicemail and earlier texts got no reply. Gentle check-in: they can reply "
               "any time with a good time to talk."),
    ("tier_final", "This is the last automated text. Say we do not want to keep bothering them; they can reply or "
                   "email {reply_to_email} whenever they are ready."),
):
    register(
        family=_FAMILY,
        version=_version,
        system_prompt=_SYSTEM,
        user_prompt_template=_COMMON.replace("{situation}", _situation),
    )
