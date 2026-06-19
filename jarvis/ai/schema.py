"""Structured data that flows between the AI core and the skill engine.

The AI core may ONLY emit :class:`IntentPacket` objects. It is never allowed to
execute anything itself — the orchestrator routes the intent to the skill
engine, which is the only place real actions happen. This separation is a hard
architectural rule (see README "Safety rules").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# §2.3 / §3: every internal intent type maps deterministically to exactly one of
# the six approved command categories (or UNKNOWN).
COMMAND_TYPES: dict[str, str] = {
    "get_time": "TIME_DATE", "get_date": "TIME_DATE",
    "open_app": "APP_CONTROL", "close_app": "APP_CONTROL",
    "system_info": "SYSTEM_STATUS",
    "web_search": "WEB_SEARCH",
    "add_note": "NOTES", "read_notes": "NOTES", "clear_notes": "NOTES",
    "list_files": "FILE_ACCESS", "find_file": "FILE_ACCESS",
    "open_folder": "FILE_ACCESS",
}


def command_type_for(intent_type: str) -> str:
    return COMMAND_TYPES.get(intent_type, "UNKNOWN")


@dataclass
class Intent:
    type: str                                   # e.g. "open_app", "web_search"
    parameters: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.parameters.get(key, default)

    @property
    def command_type(self) -> str:
        return command_type_for(self.type)


@dataclass
class IntentPacket:
    """The contract the AI core produces for every user utterance."""
    thought: str                 # private reasoning (shown only in HUD log)
    intent: Intent               # what to do
    response_text: str           # what JARVIS should say back
    source_text: str = ""        # the transcript that produced this
    needs_skill: bool = True     # False for pure conversational replies
    confidence: float = 1.0      # §3 STEP3 classification confidence

    @property
    def command_type(self) -> str:
        return self.intent.command_type

    def classification(self, requires_confirmation: bool = False) -> dict[str, Any]:
        """The §3 STEP3 structured classification record."""
        return {
            "command_type": self.command_type,
            "confidence": round(self.confidence, 2),
            "requires_confirmation": requires_confirmation,
            "requires_tool": self.needs_skill,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_text: str = "",
                  confidence: float = 1.0) -> "IntentPacket":
        raw_intent = data.get("intent") or {}
        intent = Intent(
            type=str(raw_intent.get("type", "none")),
            parameters=dict(raw_intent.get("parameters", {})),
        )
        return cls(
            thought=str(data.get("thought", "")),
            intent=intent,
            response_text=str(data.get("response_text", "")),
            source_text=source_text,
            needs_skill=intent.type not in ("none", "chat", "unknown", ""),
            confidence=float(data.get("confidence", confidence)),
        )


@dataclass
class SkillResult:
    """Returned by every skill execution and fed back to the AI/visuals."""
    ok: bool
    skill: str
    summary: str                                # short, user-facing
    data: dict[str, Any] = field(default_factory=dict)
    speak: str | None = None                    # overrides AI response_text if set
    needs_confirmation: bool = False            # sensitive action gate
