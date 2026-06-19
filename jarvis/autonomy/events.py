"""§16.2 event-stream model + the §16.3 suggestion record.

The autonomy engine reacts to :class:`ContextEvent`s (not just commands) and, when
it has something genuinely useful and safe to offer, produces a
:class:`Suggestion`. A suggestion is *advice only* — it is surfaced to the user
and never executed (§16.3 "must NEVER act without permission").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# §16.2 event types
APP_OPEN = "APP_OPEN"
APP_CLOSE = "APP_CLOSE"
IDLE = "IDLE"
RESUME = "RESUME"
FILE_ACCESS = "FILE_ACCESS"
SYSTEM_ALERT = "SYSTEM_ALERT"
TASK_DUE = "TASK_DUE"


@dataclass
class ContextEvent:
    type: str
    context: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "context": self.context,
                "timestamp": self.timestamp}


@dataclass
class Suggestion:
    """Advice surfaced to the user. Never an action."""
    text: str                      # what JARVIS would say
    reason: str = ""               # why it's being raised
    confidence: float = 0.0        # 0..1, gated by §16.3
    source_event: str = ""         # the event type that prompted it

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "reason": self.reason,
                "confidence": round(self.confidence, 2),
                "source_event": self.source_event}
