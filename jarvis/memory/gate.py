"""The §4.2 / §5-adjacent memory decision layer.

Before anything is written to the vault, the gate evaluates a completed turn
and emits a :class:`MemoryDecision` — the structured verdict the spec mandates::

    { "store": bool, "type": ..., "reason": ..., "confidence": ... }

It enforces the four §4 memory rules deterministically (no network, no model):

  * only durable / reusable information is stored,
  * transient conversational noise is dropped,
  * secrets are never stored unless explicitly allowed,
  * usefulness is always evaluated before writing.

Intent *types* that are inherently transient (the clock, system telemetry, a web
lookup) are dropped outright; durable signal comes from how the user phrases a
statement ("remember that…", "I prefer…", "I'm working on…").
"""
from __future__ import annotations

import re

from .schema import MemoryDecision, MemoryEntry

# questions are requests, not durable facts — don't store them as knowledge
# unless the user explicitly prefixed an instruction to remember.
_QUESTION = re.compile(
    r"^\s*(what|when|where|who|why|which|how|is|are|do|does|can|could|"
    r"would|will|should|did)\b", re.IGNORECASE)

# explicit "commit this to memory" cues -> highest-confidence storage.
_EXPLICIT = re.compile(
    r"\b(remember(?:\s+that|\s+to)?|note\s+that|don'?t\s+forget|"
    r"keep\s+in\s+mind|for\s+(?:future\s+)?reference|make\s+a\s+note)\b",
    re.IGNORECASE)
_EXPLICIT_STRIP = re.compile(
    r"^\s*(?:please\s+)?(?:remember(?:\s+that|\s+to)?|note\s+that|"
    r"don'?t\s+forget(?:\s+that)?|keep\s+in\s+mind(?:\s+that)?|"
    r"make\s+a\s+note(?:\s+that)?)\s*[:,]?\s*",
    re.IGNORECASE)

# durable self-statements, mapped to a §4.2 entry type.
_PREFERENCE = re.compile(
    r"\b(i\s+(?:prefer|like|love|hate|always|usually|never)|"
    r"my\s+(?:favou?rite|name\s+is|preferred)|call\s+me|i'?m\s+called)\b",
    re.IGNORECASE)
_PROJECT = re.compile(
    r"\b(i'?m\s+working\s+on|my\s+project|the\s+project|deadline|"
    r"we'?re\s+building|my\s+(?:repo|repository|codebase)|due\s+(?:on|by))\b",
    re.IGNORECASE)

# anything that smells like a secret — refuse to store (§4 rule 3, §15).
_SECRET = re.compile(
    r"\b(password|passcode|api[\s_-]?key|secret|token|ssn|"
    r"social\s+security|credit\s+card|cvv|pin\s+(?:code|number)|"
    r"private\s+key|seed\s+phrase)\b",
    re.IGNORECASE)


class MemoryGate:
    """Decides whether a turn becomes a durable memory."""

    def __init__(self, allow_secrets: bool = False, min_words: int = 3) -> None:
        self.allow_secrets = allow_secrets
        self.min_words = min_words

    def consider(self, text: str, intent_type: str,
                 ) -> tuple[MemoryDecision, MemoryEntry | None]:
        """Return the verdict and, when storing, the entry to persist."""
        text = (text or "").strip()
        if not text:
            return MemoryDecision.skip("empty input"), None

        if _SECRET.search(text) and not self.allow_secrets:
            return MemoryDecision.skip("contains a secret — refused"), None

        explicit = bool(_EXPLICIT.search(text))

        # questions are requests, not facts — skip unless explicitly told to keep.
        if _QUESTION.search(text) and not explicit:
            return MemoryDecision.skip("question, not a durable fact"), None

        # Durability is judged by *phrasing*, not by which skill ran: a
        # preference/project/explicit statement is worth keeping no matter
        # whether it routed to chat, notes, or a tool (intent_type is advisory).
        mem_type, confidence = self._classify(text, explicit)
        if mem_type is None:
            return MemoryDecision.skip(f"no durable signal ({intent_type})"), None

        content = self._clean(text)
        if len(content.split()) < self.min_words:
            return MemoryDecision.skip("too short to be useful"), None

        decision = MemoryDecision(
            store=True, type=mem_type, confidence=confidence,
            reason=("explicit user request" if explicit
                    else f"durable {mem_type} statement"))
        entry = MemoryEntry(content=content, type=mem_type, source="user",
                            confidence=confidence, tags=self._tags(mem_type, text))
        return decision, entry

    # -- classification ------------------------------------------------------
    def _classify(self, text: str, explicit: bool) -> tuple[str | None, float]:
        if _PREFERENCE.search(text):
            return "preference", 0.88 if explicit else 0.8
        if _PROJECT.search(text):
            return "project", 0.85 if explicit else 0.75
        if explicit:
            # explicit "remember X" with no obvious category -> general knowledge
            return "knowledge", 0.9
        return None, 0.0

    def _clean(self, text: str) -> str:
        stripped = _EXPLICIT_STRIP.sub("", text).strip()
        return stripped or text

    def _tags(self, mem_type: str, text: str) -> list[str]:
        tags = [mem_type]
        if re.search(r"\bdeadline|due\b", text, re.IGNORECASE):
            tags.append("deadline")
        return tags
