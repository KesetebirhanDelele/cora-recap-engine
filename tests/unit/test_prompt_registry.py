"""
Phase 5 — prompt registry tests.

Covers:
  1.  All four prompt families are registered after importing app.prompts
  2.  get_prompt() returns the correct entry for (family, version)
  3.  get_prompt() raises KeyError for unknown family or version
  4.  Each registered prompt has non-empty system_prompt and user_prompt_template
  5.  build_messages() returns [system, user] structure
  6.  build_messages() substitutes the transcript placeholder
  7.  list_versions() returns versions for a family
  8.  list_families() returns all four families
  9.  is_registered() returns True/False correctly
  10. Duplicate registration raises ValueError
  11. Prompt version metadata is immutable (frozen dataclass)
  12. All families have version v1 registered
"""
from __future__ import annotations

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _load_prompts():
    """Ensure all prompts are registered before any test runs."""
    import app.prompts  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
# 1. All four families registered
# ─────────────────────────────────────────────────────────────────────────────

def test_all_four_families_registered():
    from app.prompts import list_families
    families = list_families()
    expected = {
        "lead_stage_classifier",
        "student_summary_generator",
        "summary_consent_detector",
        "vm_content_generator",
    }
    assert expected.issubset(set(families)), f"Missing families: {expected - set(families)}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. get_prompt returns correct entry
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("family,version", [
    ("lead_stage_classifier", "v1"),
    ("student_summary_generator", "v1"),
    ("summary_consent_detector", "v1"),
    ("vm_content_generator", "v1"),
])
def test_get_prompt_returns_entry(family, version):
    from app.prompts import get_prompt
    entry = get_prompt(family, version)
    assert entry.family == family
    assert entry.version == version


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_prompt raises KeyError for unknown inputs
# ─────────────────────────────────────────────────────────────────────────────

def test_get_prompt_raises_for_unknown_family():
    from app.prompts import get_prompt
    with pytest.raises(KeyError, match="not found"):
        get_prompt("nonexistent_family", "v1")


def test_get_prompt_raises_for_unknown_version():
    from app.prompts import get_prompt
    with pytest.raises(KeyError, match="not found"):
        get_prompt("student_summary_generator", "v99")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Each prompt has non-empty content
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("family", [
    "lead_stage_classifier",
    "student_summary_generator",
    "summary_consent_detector",
    "vm_content_generator",
])
def test_prompts_have_non_empty_system_prompt(family):
    from app.prompts import get_prompt
    entry = get_prompt(family, "v1")
    assert entry.system_prompt.strip(), f"{family} system_prompt is empty"


@pytest.mark.parametrize("family", [
    "lead_stage_classifier",
    "student_summary_generator",
    "summary_consent_detector",
    "vm_content_generator",
])
def test_prompts_have_non_empty_user_template(family):
    from app.prompts import get_prompt
    entry = get_prompt(family, "v1")
    assert entry.user_prompt_template.strip(), f"{family} user_prompt_template is empty"


# ─────────────────────────────────────────────────────────────────────────────
# 5. build_messages returns correct structure
# ─────────────────────────────────────────────────────────────────────────────

def test_build_messages_returns_system_and_user():
    from app.prompts import get_prompt
    entry = get_prompt("student_summary_generator", "v1")
    messages = entry.build_messages(transcript="Hello, this is a test.")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_messages_system_content_non_empty():
    from app.prompts import get_prompt
    entry = get_prompt("student_summary_generator", "v1")
    messages = entry.build_messages(transcript="Test transcript.")
    assert messages[0]["content"].strip()


# ─────────────────────────────────────────────────────────────────────────────
# 6. build_messages substitutes transcript
# ─────────────────────────────────────────────────────────────────────────────

def test_build_messages_substitutes_transcript():
    from app.prompts import get_prompt
    entry = get_prompt("student_summary_generator", "v1")
    transcript = "UNIQUE_SENTINEL_VALUE_FOR_TEST"
    messages = entry.build_messages(transcript=transcript)
    user_content = messages[1]["content"]
    assert transcript in user_content


def test_build_messages_substitutes_context_for_vm_content():
    from app.prompts import get_prompt
    entry = get_prompt("vm_content_generator", "v1")
    context = "CONTEXT_SENTINEL_12345"
    messages = entry.build_messages(context=context)
    user_content = messages[1]["content"]
    assert context in user_content


def test_build_messages_raises_for_missing_placeholder():
    from app.prompts import get_prompt
    entry = get_prompt("student_summary_generator", "v1")
    with pytest.raises(KeyError):
        entry.build_messages()  # missing 'transcript'


# ─────────────────────────────────────────────────────────────────────────────
# 7. list_versions
# ─────────────────────────────────────────────────────────────────────────────

def test_list_versions_returns_v1_for_all_families():
    from app.prompts import list_versions
    for family in [
        "lead_stage_classifier",
        "student_summary_generator",
        "summary_consent_detector",
        "vm_content_generator",
    ]:
        versions = list_versions(family)
        assert "v1" in versions, f"v1 not registered for {family}"


def test_list_versions_returns_empty_for_unknown_family():
    from app.prompts import list_versions
    assert list_versions("nonexistent_family") == []


# ─────────────────────────────────────────────────────────────────────────────
# 8. list_families
# ─────────────────────────────────────────────────────────────────────────────

def test_list_families_returns_sorted_list():
    from app.prompts import list_families
    families = list_families()
    assert families == sorted(families)


# ─────────────────────────────────────────────────────────────────────────────
# 9. is_registered
# ─────────────────────────────────────────────────────────────────────────────

def test_is_registered_true_for_known():
    from app.prompts import is_registered
    assert is_registered("student_summary_generator", "v1") is True


def test_is_registered_false_for_unknown_version():
    from app.prompts import is_registered
    assert is_registered("student_summary_generator", "v99") is False


def test_is_registered_false_for_unknown_family():
    from app.prompts import is_registered
    assert is_registered("nonexistent", "v1") is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. Duplicate registration raises ValueError
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_registration_raises():
    from app.prompts.registry import register
    with pytest.raises(ValueError, match="already registered"):
        register("student_summary_generator", "v1", "system", "user {transcript}")


# ─────────────────────────────────────────────────────────────────────────────
# 11. PromptEntry is immutable
# ─────────────────────────────────────────────────────────────────────────────

def test_prompt_entry_is_frozen():
    from app.prompts import get_prompt
    entry = get_prompt("student_summary_generator", "v1")
    with pytest.raises((AttributeError, TypeError)):
        entry.system_prompt = "mutated"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# 12. All families have v1
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("family", [
    "lead_stage_classifier",
    "student_summary_generator",
    "summary_consent_detector",
    "vm_content_generator",
])
def test_all_families_have_v1(family):
    from app.prompts import is_registered
    assert is_registered(family, "v1")
