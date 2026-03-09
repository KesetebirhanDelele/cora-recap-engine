"""
Prompt family: student_summary_generator

Generates a concise student-facing summary of an admissions advising call.
Returns blank summary when transcript is blank or not a real conversation.

v1: initial production version.
"""
from app.prompts.registry import register

_FAMILY = "student_summary_generator"

register(
    family=_FAMILY,
    version="v1",
    system_prompt=(
        "You are an admissions advising assistant. Generate a brief student-facing "
        "summary of this advising call in 1-3 sentences. The summary should be "
        "written for the student, not the advisor.\n\n"
        "Return a JSON object with exactly these fields:\n"
        '- "student_summary": string — the student-facing summary '
        "(empty string if transcript is blank, unusable, or not a real conversation)\n"
        '- "summary_offered": boolean — true if the advisor offered to send a '
        "written summary to the student during the call\n\n"
        "If the transcript is blank, contains only noise, or is not a real "
        "advising conversation, return: "
        '{"student_summary": "", "summary_offered": false}\n\n'
        "Return only valid JSON. Do not include text outside the JSON object."
    ),
    user_prompt_template=(
        "Transcript:\n{transcript}"
    ),
)
