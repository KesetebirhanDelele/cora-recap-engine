"""
Prompt family: lead_stage_classifier

Classifies a call transcript into lead stage and call outcome for CRM routing.

v1: initial production version.
"""
from app.prompts.registry import register

_FAMILY = "lead_stage_classifier"

register(
    family=_FAMILY,
    version="v1",
    system_prompt=(
        "You are an admissions CRM analyst. Classify the call transcript below "
        "and return a JSON object with exactly these fields:\n"
        '- "lead_stage": one of "New Lead", "Cold Lead", "Unknown"\n'
        '- "call_outcome": one of "completed", "voicemail", "no_answer", "hangup", "other"\n'
        '- "key_topics": list of strings, up to 5 topics discussed (empty list if none)\n\n'
        "Return only valid JSON. Do not include explanations outside the JSON object."
    ),
    user_prompt_template=(
        "Transcript:\n{transcript}"
    ),
)
