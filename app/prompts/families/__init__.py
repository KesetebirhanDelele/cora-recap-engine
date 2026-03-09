"""
Prompt families — importing this package registers all family prompts.

Each submodule calls registry.register() at import time.
Import order determines registration order (no functional dependency).
"""
from app.prompts.families import (
    lead_stage_classifier,
    student_summary_generator,
    summary_consent_detector,
    vm_content_generator,
)

__all__ = [
    "lead_stage_classifier",
    "student_summary_generator",
    "summary_consent_detector",
    "vm_content_generator",
]
