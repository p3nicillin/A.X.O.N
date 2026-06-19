"""The mandatory contract every skill must implement.

A skill is a self-contained, versioned, independently testable unit. It can
only act through the parameters handed to it in an :class:`Intent`; it never
receives the raw microphone, the AI client, or unrestricted OS handles. The
registry loads each skill's ``manifest.json`` for metadata and capability
declarations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..ai.schema import Intent, SkillResult


@dataclass
class SkillManifest:
    name: str
    version: str
    description: str
    intents: list[str]          # intent types this skill claims
    sensitive: bool = False     # requires user confirmation before executing
    author: str = "core"


class Skill(ABC):
    """Base class for all skills. Subclasses live in their own package."""

    manifest: SkillManifest

    def can_handle(self, intent: Intent) -> bool:
        """Default: handle any intent type declared in the manifest."""
        return intent.type in self.manifest.intents

    @abstractmethod
    def execute(self, intent: Intent) -> SkillResult:
        """Perform the action and return a structured result.

        Implementations MUST NOT raise for expected failures — return a
        ``SkillResult(ok=False, ...)`` instead so the pipeline stays alive.
        """
        raise NotImplementedError

    # convenience builders ---------------------------------------------------
    def ok(self, summary: str, *, speak: str | None = None, **data) -> SkillResult:
        return SkillResult(ok=True, skill=self.manifest.name, summary=summary,
                           data=data, speak=speak)

    def fail(self, summary: str, *, speak: str | None = None, **data) -> SkillResult:
        return SkillResult(ok=False, skill=self.manifest.name, summary=summary,
                           data=data, speak=speak)
