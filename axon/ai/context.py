"""Short rolling conversation memory handed to the AI core each turn."""
from __future__ import annotations

from collections import deque


class Context:
    def __init__(self, max_turns: int = 8) -> None:
        self._turns: deque[tuple[str, str]] = deque(maxlen=max_turns)
        # §4.3 facts recalled from long-term memory for the current turn only.
        self.recalled: list[str] = []
        # §17 a short user-profile hint for the current turn.
        self.user_hint: str = ""
        # Turn-scoped desktop state. This is never persisted as conversation.
        self.desktop_hint: str = ""

    def add(self, user: str, assistant: str) -> None:
        self._turns.append((user, assistant))

    def set_recalled(self, facts: list[str]) -> None:
        """Replace the per-turn recalled memory (cleared/overwritten each turn)."""
        self.recalled = list(facts)

    def set_user_hint(self, hint: str) -> None:
        """Replace the per-turn §17 user-profile hint."""
        self.user_hint = hint or ""

    def set_desktop_hint(self, hint: str) -> None:
        """Replace the current active-window hint used by intent backends."""
        self.desktop_hint = hint or ""

    def as_messages(self) -> list[dict]:
        msgs: list[dict] = []
        for user, assistant in self._turns:
            msgs.append({"role": "user", "content": user})
            if assistant:
                msgs.append({"role": "assistant", "content": assistant})
        return msgs

    def recent_text(self) -> str:
        return " ".join(u for u, _ in self._turns)
