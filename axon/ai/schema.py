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
    "list_running_apps": "SYSTEM_STATUS", "network_status": "SYSTEM_STATUS",
    "web_search": "WEB_SEARCH", "research_web": "WEB_SEARCH",
    "read_webpage": "WEB_SEARCH",
    "open_website": "WEB_NAVIGATION",
    "search_browser": "WEB_NAVIGATION", "open_browser": "WEB_NAVIGATION",
    "browser_action": "WEB_NAVIGATION",
    "get_weather": "WEATHER",
    "calculate": "UTILITY",
    "add_note": "NOTES", "read_notes": "NOTES", "clear_notes": "NOTES",
    "list_files": "FILE_ACCESS", "find_file": "FILE_ACCESS",
    "open_folder": "FILE_ACCESS",
    "play_pause": "MEDIA_CONTROL", "next_track": "MEDIA_CONTROL",
    "previous_track": "MEDIA_CONTROL",
    "volume_up": "VOLUME_CONTROL", "volume_down": "VOLUME_CONTROL",
    "mute_toggle": "VOLUME_CONTROL",
    "minimize_window": "WINDOW_CONTROL", "maximize_window": "WINDOW_CONTROL",
    "restore_window": "WINDOW_CONTROL",
    "focus_window": "WINDOW_CONTROL", "close_window": "WINDOW_CONTROL",
    "read_clipboard": "CLIPBOARD", "set_clipboard": "CLIPBOARD",
    "get_active_window": "WINDOW_CONTROL", "list_windows": "WINDOW_CONTROL",
    "set_timer": "REMINDERS", "set_reminder": "REMINDERS",
    "list_reminders": "REMINDERS", "cancel_reminder": "REMINDERS",
    "inspect_screen": "SCREEN_PERCEPTION",
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
    response_text: str           # what AXON should say back
    source_text: str = ""        # the transcript that produced this
    needs_skill: bool = True     # False for pure conversational replies
    confidence: float = 1.0      # §3 STEP3 classification confidence
    # --- provenance (§7 observability / §8 privacy) ---
    backend: str = ""            # which IntentBackend produced this packet
    model: str = ""              # model name (local model id or cloud model)
    latency_ms: float = 0.0      # backend parse latency
    repaired: bool = False       # True if a JSON-repair retry was needed
    cloud_routed: bool = False   # §8 True iff this left the device (cloud backend)

    @property
    def command_type(self) -> str:
        return self.intent.command_type

    def tag(self, *, backend: str = "", model: str = "", latency_ms: float = 0.0,
            repaired: bool = False, cloud_routed: bool = False) -> "IntentPacket":
        """Stamp provenance onto the packet (returns self for chaining)."""
        self.backend = backend or self.backend
        self.model = model or self.model
        self.latency_ms = latency_ms or self.latency_ms
        self.repaired = repaired or self.repaired
        self.cloud_routed = cloud_routed or self.cloud_routed
        return self

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
            needs_skill=intent.type not in (
                "none", "chat", "answer", "unknown", ""),
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
