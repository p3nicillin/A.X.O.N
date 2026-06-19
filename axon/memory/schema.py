"""Structured records for the §4 memory system.

Two dataclasses cross the memory boundary:

  * :class:`MemoryEntry`    — one durable fact in the episodic vault (§4.2). It
    serialises to/from the markdown entry format AXON already uses for notes.
  * :class:`MemoryDecision` — the L4 gate verdict (§4.2 "Memory Decision
    Output"): should this turn be remembered at all, and how.

Nothing here touches disk or the network; that is the store's job. Keeping the
records pure makes the gate and store independently testable.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# §4.2 the only legal episodic entry kinds. The gate must pick one of these.
MEMORY_TYPES = ("preference", "project", "system", "task", "knowledge")
# legal provenance for an entry (§4.2 "Source")
MEMORY_SOURCES = ("user", "system", "tool")


@dataclass
class MemoryEntry:
    """One durable fact in the episodic vault (§4.2)."""
    content: str
    type: str = "knowledge"            # one of MEMORY_TYPES
    source: str = "user"               # one of MEMORY_SOURCES
    confidence: float = 0.8            # 0..1, how sure we are it's worth keeping
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    tags: list[str] = field(default_factory=list)
    id: str = ""                       # stable slug; derived if left blank

    def __post_init__(self) -> None:
        if self.type not in MEMORY_TYPES:
            self.type = "knowledge"
        if self.source not in MEMORY_SOURCES:
            self.source = "user"
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if not self.id:
            self.id = self._derive_id()

    def _derive_id(self) -> str:
        """A filesystem-safe, content-stable id so re-storing the same fact
        overwrites rather than duplicates."""
        digest = hashlib.sha1(self.content.strip().lower().encode("utf-8"))
        slug = re.sub(r"[^a-z0-9]+", "-", self.content.strip().lower())[:40].strip("-")
        return f"{slug or 'entry'}-{digest.hexdigest()[:8]}"

    # -- markdown serialisation (§4 entry format) ---------------------------
    def to_markdown(self) -> str:
        tags = ", ".join(self.tags)
        return (
            "# Entry\n\n"
            f"## Type\n{self.type}\n\n"
            f"## Content\n{self.content}\n\n"
            f"## Source\n{self.source}\n\n"
            f"## Confidence\n{self.confidence:.2f}\n\n"
            f"## Timestamp\n{self.timestamp}\n\n"
            f"## Tags\n{tags}\n"
        )

    @classmethod
    def from_markdown(cls, text: str, entry_id: str = "") -> "MemoryEntry | None":
        def section(name: str) -> str:
            m = re.search(rf"^##\s+{name}\s*\n(.*?)(?=\n##\s|\Z)", text,
                          re.MULTILINE | re.DOTALL)
            return m.group(1).strip() if m else ""

        content = section("Content")
        if not content:
            return None
        raw_tags = section("Tags")
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        try:
            confidence = float(section("Confidence") or 0.8)
        except ValueError:
            confidence = 0.8
        return cls(
            content=content,
            type=section("Type") or "knowledge",
            source=section("Source") or "user",
            confidence=confidence,
            timestamp=section("Timestamp") or "",
            tags=tags,
            id=entry_id,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "source": self.source,
            "confidence": round(self.confidence, 2), "timestamp": self.timestamp,
            "content": self.content, "tags": self.tags,
        }


@dataclass
class MemoryDecision:
    """The L4 gate output (§4.2). ``store`` False means the turn is transient
    noise and must not be persisted."""
    store: bool
    type: str = "none"                 # MEMORY_TYPES or "none"
    reason: str = ""
    confidence: float = 0.0
    source: str = "user"

    def as_dict(self) -> dict[str, Any]:
        return {
            "store": self.store, "type": self.type,
            "reason": self.reason, "confidence": round(self.confidence, 2),
        }

    @classmethod
    def skip(cls, reason: str) -> "MemoryDecision":
        return cls(store=False, type="none", reason=reason, confidence=0.0)
