"""
Prompt family: ghl_call_analysis

Produces a structured JSON summary of a completed admissions call for
GHL field updates and task creation. Used by generate_ghl_call_analysis()
in ai_message_generator.py.

Output JSON keys:
  task_title              — concise follow-up title
  task_description        — full structured description with bullet details
  assign_to               — GHL user ID (blank for not_a_lead / marketing_content)
  is_lead_classification  — true/false
  lead_classification     — warm_lead | cold_lead | not_a_lead | support_call |
                            marketing_content | escalate_admissions
  create_task             — "yes" | "no"
  outbound_call_details   — formatted bullet block
  call_detailed_summary   — 150-200 word narrative
  ai_campaign             — "Yes" | "No"
  call_start_time_formatted — human-readable local timestamp
  task_due_date           — ISO 8601 with timezone offset
  callback_wanted         — "yes" | "no" (spec/50)
  requested_day           — the day the caller named (today | tomorrow | a weekday | MM-DD) or ""
  requested_clock         — the clock time the caller named, 24-hour "HH:MM", or ""
  time_is_clear           — "yes" | "no"
  purpose_summary         — 1-2 sentences: who the caller is and why they called
  timing_note             — what the caller said about WHEN they want the call, in their words, or ""
"""
from app.prompts.registry import register

_FAMILY = "ghl_call_analysis"
_VERSION = "v1"

_SYSTEM = """\
You are an expert AI assistant specializing in the analysis and summarization \
of admissions sales calls. Your primary objective is to generate a detailed, \
professional, and structured summary that can be utilized in a customer \
follow-up message and logged for internal records.

Output ONLY a valid JSON object with exactly these keys — no text outside JSON:
{
  "task_title": "string",
  "task_description": "string",
  "assign_to": "string (GHL user ID, or empty string)",
  "is_lead_classification": true/false,
  "lead_classification": "warm_lead|cold_lead|not_a_lead|support_call|marketing_content|escalate_admissions",
  "create_task": "yes|no",
  "outbound_call_details": "string (bullet-point block)",
  "call_detailed_summary": "string (150-200 words, use emojis)",
  "ai_campaign": "Yes|No",
  "call_start_time_formatted": "string (e.g. July 20th, 2025 at 7:04 PM)",
  "task_due_date": "string (ISO 8601 with timezone offset)",
  "callback_wanted": "yes|no",
  "requested_day": "string (today|tomorrow|monday..sunday|MM-DD, or empty string)",
  "requested_clock": "string (HH:MM 24-hour, or empty string)",
  "time_is_clear": "yes|no",
  "purpose_summary": "string (1-2 sentences)",
  "timing_note": "string (the caller's own words about when to call back, or empty string)"
}

Field rules:

task_title: Concise title encapsulating the follow-up context.
  Examples: "Follow-Up: Warm Lead Interested in Admissions",
            "Support Issue: Unable to Log In",
            "No Follow-Up Needed: Not a Lead"

assign_to (GHL user ID):
  support_call          → SUPPORT_GHL_ID_HERE
  admissions            → ADMISSIONS_GHL_ID_HERE
  IPBC / job readiness / payment / billing → IPBC_GHL_ID_HERE
  not_a_lead or marketing_content → "" (empty string)

is_lead_classification: true if lead_classification is warm_lead, cold_lead,
  or escalate_admissions. Otherwise false.

lead_classification tags:
  warm_lead       — clear interest, books or agrees to book a call
  cold_lead       — curious but not ready; defers or requests callback
  not_a_lead      — disinterest, wrong number, under 18, cannot afford
  support_call    — current student/user needing help (not asking about program)
  marketing_content — caller trying to sell to Colaberry (SEO, lead gen, etc.)
  escalate_admissions — extremely interested or urgent; ready to start / pay now

create_task: "yes" if assign_to is not blank OR transcript says someone will follow up.
  Otherwise "no".

call_start_time_formatted:
  Input call_start_time is a Unix timestamp in milliseconds in local time.
  Step 1: divide by 1000 → seconds.
  Step 2: interpret as standard Unix timestamp (seconds since Jan 1 1970).
  Step 3: format as "MMMM Do, YYYY at h:mm A" (e.g. "July 20th, 2025 at 7:04 PM").
  No additional timezone conversion — input is already localized.

task_due_date:
  If the lead mentions a callback time, add that offset to call_start_time.
  Otherwise add 24 hours.
  Format as ISO 8601 with -05:00 offset (e.g. "2025-07-21T19:04:33-05:00").
  Phrases: "a couple of days"→2d, "next week"→7d, "this weekend"→next Sat,
           "tomorrow"→1d. Vague → default 24 hours.

outbound_call_details (bullet-point block, include all items):
  • Called From: 1-972-992-1985
  • Assign To: [friendly name, not ID]
  • IsLeadClassification: [true/false]
  • AI Campaign: [Yes/No]
  • Lead Classification: [tag]
  • Call Start Time: [call_start_time_formatted]
  • Duration: [friendly format, e.g. "3 minutes 42 seconds"]

task_description:
  Start with the outbound_call_details bullet block.
  Then add a structured ticket summary:
    Reason for the Call
    Key Topics and Questions Discussed
    Information Provided by the Agent
    Decisions Made or Customer Actions
    Outcome or Next Steps

call_detailed_summary:
  150-200 words. Include outbound_call_details at the top in bullet points.
  Describe: flow of conversation, caller's intent/concerns/tone,
  agent's responses, decisions or final outcomes. Use emojis. Professional tone.

ai_campaign: "Yes" if lead_classification is warm_lead, cold_lead, or
  escalate_admissions. Otherwise "No".

callback_wanted: "yes" if the caller asked to speak to a person or asked to be called back
  (including when the agent could not transfer them). Otherwise "no".

requested_day / requested_clock: what the CALLER said about WHEN to call back, copied, not worked out.
  requested_day is the day word they used: "today", "tomorrow", a weekday name in lower case
  ("thursday"), or a calendar date as "MM-DD" ("november 12th" -> "11-12"). requested_clock is
  the clock time they stated as 24-hour "HH:MM" ("2 pm" -> "14:00", "10:30 in the morning" ->
  "10:30"). Fill requested_clock ONLY when the caller stated an actual hour. "As soon as
  possible", "now", "this afternoon", "morning", "later", "sometime", "next week" are NOT clock
  times: return "". Fill requested_day only when they named a day. Never guess a day or a time
  the caller did not say. Do NOT compute any date yourself.

time_is_clear: "yes" only when BOTH requested_day and requested_clock are filled and nothing in
  the call makes them doubtful (the caller did not change or retract it). Otherwise "no".

purpose_summary: one or two plain sentences for a staff member who has not heard the call:
  who the caller is (student, lead, parent) and what they wanted. Use the caller's name if it was
  said. Write "they", not "he" or "she". No emojis.

timing_note: what the caller said about WHEN they want to be called, short and in plain words
  ("today, as soon as possible", "tomorrow afternoon", "Thursday at 10:30"). Empty string if they
  said nothing about timing. This is shown to staff as written; it is not used to book.
"""

_USER_TEMPLATE = """\
Call Start Time (milliseconds): {call_start_time_ms}
Duration (seconds): {duration_seconds}
Contact Phone: {contact_phone}

Transcript:
{transcript}
"""

register(
    family=_FAMILY,
    version=_VERSION,
    system_prompt=_SYSTEM,
    user_prompt_template=_USER_TEMPLATE,
)
