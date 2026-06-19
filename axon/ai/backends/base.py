"""The pluggable AI-core backend contract.

Every backend has exactly one job: turn a transcript into a schema-valid
:class:`IntentPacket`. It either returns one, or raises :class:`IntentBackendError`.
A backend NEVER returns malformed data and NEVER executes anything — the skill
engine remains the sole action surface (the v1 capability boundary is preserved).

The set of intents a backend is allowed to emit is sourced dynamically from the
skill registry as a list of :class:`IntentSpec`, so the prompt and JSON schema
are generated from a single source of truth and adding a skill never requires
editing the AI core.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..context import Context
from ..schema import IntentPacket, command_type_for


class IntentBackendError(Exception):
    """A backend could not produce a valid IntentPacket.

    Raised for any failure mode — runtime unreachable, timeout, malformed or
    schema-invalid output that could not be repaired, missing key/model. The
    router catches it, audits the ``reason``, and falls through the chain.
    """

    def __init__(self, reason: str, *, backend: str = "", retryable: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.backend = backend
        self.retryable = retryable


@dataclass(frozen=True)
class IntentSpec:
    """One intent the AI core is permitted to emit, plus the metadata needed to
    describe it to a model and validate its output."""
    name: str
    description: str
    parameters: list[str] = field(default_factory=list)
    sensitive: bool = False

    @property
    def command_type(self) -> str:
        return command_type_for(self.name)


# The two universal, tool-less intents every backend may always emit. They are
# not owned by a skill — "chat" is small-talk, "unknown" is the safe escape hatch
# so a model never has to hallucinate a capability that doesn't exist (§2.4).
CHAT_SPEC = IntentSpec("chat", "Greetings, thanks, or small talk — no action.")
UNKNOWN_SPEC = IntentSpec(
    "unknown", "The request matches no available capability. The safe escape "
    "hatch; never invent an intent that is not listed.")


def specs_from_catalogue(catalogue) -> list[IntentSpec]:
    """Build the allowed-intent list from skill manifests (single source of
    truth). ``catalogue`` is a list of SkillManifest."""
    specs: list[IntentSpec] = []
    for m in catalogue:
        for intent in m.intents:
            specs.append(IntentSpec(
                name=intent,
                description=m.description,
                parameters=m.params_for(intent),
                sensitive=m.sensitive,
            ))
    return specs


def all_specs(catalogue) -> list[IntentSpec]:
    """Skill intents plus the universal chat/unknown intents."""
    return specs_from_catalogue(catalogue) + [CHAT_SPEC, UNKNOWN_SPEC]


class IntentBackend(ABC):
    """Base class for every AI-core backend."""

    #: short stable identifier used in audit/metrics, e.g. "local", "cloud".
    name: str = "backend"

    @abstractmethod
    def parse(self, transcript: str, context: Context,
              allowed_intents: list[IntentSpec]) -> IntentPacket:
        """Return a schema-valid IntentPacket or raise IntentBackendError."""
        raise NotImplementedError

    def available(self) -> bool:
        """Cheap health check. Default: always available (override for backends
        that depend on a runtime/key). Must not raise."""
        return True

    @property
    def model_name(self) -> str:
        """Human-readable model identifier for diagnostics (override)."""
        return ""
