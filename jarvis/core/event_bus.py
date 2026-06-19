"""A tiny thread-safe publish/subscribe bus.

Every layer runs on its own thread (mic callback, STT worker, AI worker, the
Tk UI thread). They never call each other directly; they publish typed events
and the orchestrator/visual engine react. This keeps the layers decoupled and
makes the whole pipeline observable and testable.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Event(str, Enum):
    # perception
    AUDIO_LEVEL = "audio_level"        # payload: float rms 0..1 (drives visuals)
    SPEECH_START = "speech_start"      # payload: None
    SPEECH_END = "speech_end"          # payload: None
    TRANSCRIPT = "transcript"          # payload: {text, confidence}
    WAKE_WORD = "wake_word"            # payload: None

    # ai
    INTENT = "intent"                  # payload: IntentPacket
    AI_ERROR = "ai_error"              # payload: str

    # skills
    SKILL_RESULT = "skill_result"      # payload: SkillResult

    # audio out
    SPEAK_START = "speak_start"        # payload: {text}
    SPEAK_LEVEL = "speak_level"        # payload: float 0..1 (TTS amplitude)
    SPEAK_END = "speak_end"            # payload: None

    # system
    STATE_CHANGED = "state_changed"    # payload: JarvisState
    LOG = "log"                        # payload: {level, source, message}
    COMMAND_LOG = "command_log"        # payload: §11 {wake_detected,intent,skill_used,success,...}

    # autonomy (§16)
    CONTEXT_EVENT = "context_event"    # payload: §16.2 {type, context, timestamp}
    SUGGESTION = "suggestion"          # payload: §16.3 {text, reason, confidence, ...}


@dataclass
class Message:
    event: Event
    payload: Any = None


Subscriber = Callable[[Message], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[Event, list[Subscriber]] = defaultdict(list)
        self._any: list[Subscriber] = []
        self._lock = threading.RLock()

    def subscribe(self, event: Event, fn: Subscriber) -> None:
        with self._lock:
            self._subs[event].append(fn)

    def subscribe_all(self, fn: Subscriber) -> None:
        with self._lock:
            self._any.append(fn)

    def publish(self, event: Event, payload: Any = None) -> None:
        msg = Message(event, payload)
        with self._lock:
            handlers = list(self._subs.get(event, ())) + list(self._any)
        for fn in handlers:
            try:
                fn(msg)
            except Exception as exc:  # a bad subscriber must never kill the bus
                print(f"[event_bus] subscriber error on {event}: {exc!r}")
