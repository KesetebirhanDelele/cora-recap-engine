"""
Prompt family: vm_content_generator

Generates follow-up content for voicemail outreach campaigns.
The generated content populates GHL contact fields used by GHL automations
for email and SMS delivery — this app does NOT send directly.

v1: initial production version.
"""
from app.prompts.registry import register

_FAMILY = "vm_content_generator"

register(
    family=_FAMILY,
    version="v1",
    system_prompt=(
        "You are an admissions communications writer. Generate follow-up content "
        "for a prospective student who did not answer an admissions call.\n\n"
        "Return a JSON object with exactly these fields:\n"
        '- "email_subject": concise email subject line (under 60 characters)\n'
        '- "email_html": HTML-formatted email body (professional, warm, brief)\n'
        '- "sms_text": brief SMS text (under 160 characters, no HTML)\n\n'
        "The content should be friendly and focused on reconnecting with the student. "
        "Do not fabricate program details not provided in the context.\n\n"
        "Return only valid JSON. Do not include text outside the JSON object."
    ),
    user_prompt_template=(
        "Context:\n{context}"
    ),
)
