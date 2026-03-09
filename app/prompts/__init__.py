"""
Prompts package — versioned prompt registry for all AI families.

Importing this package registers all four prompt families.
Use get_prompt() to retrieve a prompt by family and version.

Usage:
    from app.prompts import get_prompt

    entry = get_prompt("student_summary_generator", "v1")
    messages = entry.build_messages(transcript="Call transcript here...")
"""
import app.prompts.families  # noqa: F401 — side-effect: registers all families
from app.prompts.registry import (
    get_prompt,
    is_registered,
    list_families,
    list_versions,
    register,
)

__all__ = [
    "get_prompt",
    "register",
    "list_versions",
    "list_families",
    "is_registered",
]
