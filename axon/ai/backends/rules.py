"""RuleBackend — the deterministic keyword parser behind the IntentBackend
interface.

It plays two roles:
  * the final, always-available fallback in the chain (it never raises and never
    needs a network or a key), and
  * the hybrid fast-path matcher (§5): simple, unambiguous commands are matched
    here and skip the LLM entirely, saving latency and compute.

The actual matching logic lives in the battle-tested :class:`LocalIntentEngine`;
this is a thin adapter over it.
"""
from __future__ import annotations

from ..context import Context
from ..schema import IntentPacket
from .base import IntentBackend, IntentSpec

# Intents simple and unambiguous enough that a rule match is as good as an LLM
# parse — these short-circuit the model. Free-text intents (web_search, add_note,
# find_file) and the chat/unknown fallbacks are NOT here, so they still get the
# LLM's better natural-language handling when one is available.
FAST_PATH_INTENTS = frozenset({
    "get_time", "get_date", "system_info", "list_running_apps",
    "network_status",
    "open_app", "close_app", "read_notes", "clear_notes", "list_files",
    "read_file", "write_file", "create_folder", "move_path", "delete_path",
    "open_folder",
    "play_pause", "next_track", "previous_track",
    "volume_up", "volume_down", "mute_toggle",
    "focus_window", "minimize_window", "maximize_window", "restore_window",
    "close_window", "get_active_window", "list_windows",
    "read_clipboard",
    "capture_screenshot",
    "inspect_screen",
    "get_weather",
    "calculate",
    "open_website",
    "search_browser", "open_browser", "browser_action",
    "read_webpage",
    "set_timer", "set_reminder", "list_reminders", "cancel_reminder",
})


class RuleBackend(IntentBackend):
    name = "rules"

    def __init__(self, engine) -> None:
        # engine is a LocalIntentEngine (duck-typed: .interpret(text, context))
        self._engine = engine

    @property
    def model_name(self) -> str:
        return "rule-based"

    def parse(self, transcript: str, context: Context,
              allowed_intents: list[IntentSpec]) -> IntentPacket:
        # The rule engine always returns a valid packet (worst case: unknown),
        # so it never raises — it is the guaranteed bottom of the fallback chain.
        return self._engine.interpret(transcript, context)

    def fast_path(self, transcript: str, context: Context) -> IntentPacket | None:
        """Return a packet only for a simple, high-confidence command, else None
        so the request escalates to the LLM."""
        packet = self._engine.interpret(transcript, context)
        if packet.intent.type in FAST_PATH_INTENTS:
            return packet
        return None
