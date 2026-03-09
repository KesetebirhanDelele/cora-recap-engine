"""
Prompt registry — source-controlled, versioned prompt store.

Prompts are registered at module import time by each family module.
The registry is a module-level dict keyed by (family, version).

Each registered prompt entry stores:
  - system_prompt: the system message text
  - user_prompt_template: an f-string template; call .format(**kwargs)

Usage:
    from app.prompts.registry import get_prompt

    entry = get_prompt("student_summary_generator", "v1")
    messages = entry.build_messages(transcript="Hello...")

Rollback: to revert a prompt, change the version default in settings
and redeploy. The old version remains registered and usable.
Shadow testing: run two versions side-by-side by calling get_prompt()
with different version strings.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptEntry:
    family: str
    version: str
    system_prompt: str
    user_prompt_template: str

    def build_messages(self, **kwargs: str) -> list[dict[str, str]]:
        """
        Render the prompt into OpenAI chat messages.

        kwargs: variables substituted into user_prompt_template.
        Returns: [{"role": "system", ...}, {"role": "user", ...}]
        """
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt_template.format(**kwargs)},
        ]


# Module-level registry: (family, version) → PromptEntry
_registry: dict[tuple[str, str], PromptEntry] = {}


def register(
    family: str,
    version: str,
    system_prompt: str,
    user_prompt_template: str,
) -> None:
    """Register a prompt version. Called by family modules at import time."""
    key = (family, version)
    if key in _registry:
        raise ValueError(
            f"Prompt already registered: family={family!r} version={version!r}. "
            "Each (family, version) pair must be unique."
        )
    _registry[key] = PromptEntry(
        family=family,
        version=version,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
    )


def get_prompt(family: str, version: str) -> PromptEntry:
    """
    Retrieve a registered prompt entry.

    Raises KeyError when the (family, version) pair is not found.
    This surfaces mis-configured prompt_family/version settings early,
    before any API call is attempted.
    """
    key = (family, version)
    if key not in _registry:
        registered = [f"{f}@{v}" for f, v in sorted(_registry)]
        raise KeyError(
            f"Prompt not found: {family!r}@{version!r}. "
            f"Registered: {registered}"
        )
    return _registry[key]


def list_versions(family: str) -> list[str]:
    """Return all registered versions for a prompt family, sorted."""
    return sorted(v for f, v in _registry if f == family)


def list_families() -> list[str]:
    """Return all registered prompt family names, unique and sorted."""
    return sorted({f for f, _ in _registry})


def is_registered(family: str, version: str) -> bool:
    """Return True if the (family, version) pair is registered."""
    return (family, version) in _registry
