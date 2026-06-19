"""Interruptible text-to-speech.

A single worker thread owns the speech engine (pyttsx3 / Windows SAPI5). The
public API is thread-safe:

    tts.speak("...")   # enqueue and speak
    tts.stop()         # barge-in: cut off whatever is being said right now

While speaking it publishes SPEAK_START, per-word SPEAK_LEVEL pulses (so the
core's waveform syncs to the voice) and SPEAK_END. If pyttsx3 is unavailable it
degrades to a timed simulation that still drives the animation.
"""
from __future__ import annotations

import queue
import threading
import time

from ..config import Config
from ..core.event_bus import Event, EventBus

try:
    import pyttsx3
except Exception:  # pragma: no cover - optional
    pyttsx3 = None

try:
    import pythoncom  # COM init for the worker thread on Windows
except Exception:
    pythoncom = None


class TtsEngine:
    def __init__(self, config: Config, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self.available = pyttsx3 is not None
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._engine = None
        self._speaking = threading.Event()
        self._interrupt = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._worker.start()

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._q.put(text)

    def stop(self) -> None:
        """Barge-in: clear the queue and cut current utterance."""
        with self._q.mutex:
            self._q.queue.clear()
        if self._speaking.is_set():
            self._interrupt.set()
            if self._engine is not None:
                try:
                    self._engine.stop()
                except Exception:
                    pass

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    # -- worker thread -------------------------------------------------------
    def _run(self) -> None:
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass
        if self.available:
            try:
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", self.config.tts_rate)
                self._select_voice()
                self._engine.connect("started-word", self._on_word)
            except Exception as exc:
                print(f"[tts] init failed, falling back to simulation: {exc}")
                self.available = False

        while True:
            text = self._q.get()
            if text is None:
                return
            self._interrupt.clear()
            self._speaking.set()
            self.bus.publish(Event.SPEAK_START, {"text": text})
            try:
                if self.available:
                    self._engine.say(text)
                    self._engine.runAndWait()
                else:
                    self._simulate(text)
            except Exception as exc:
                print(f"[tts] speak error: {exc}")
            finally:
                self._speaking.clear()
                self.bus.publish(Event.SPEAK_END)

    def _select_voice(self) -> None:
        want = self.config.tts_voice.strip().lower()
        if not want:
            return
        for v in self._engine.getProperty("voices"):
            if want in v.name.lower():
                self._engine.setProperty("voice", v.id)
                return

    def _on_word(self, name, location, length) -> None:
        # pulse the waveform on each spoken word
        self.bus.publish(Event.SPEAK_LEVEL, 0.85)

    def _simulate(self, text: str) -> None:
        words = text.split()
        per = 60.0 / max(1, self.config.tts_rate)
        for _ in words:
            if self._interrupt.is_set():
                break
            self.bus.publish(Event.SPEAK_LEVEL, 0.8)
            time.sleep(per)
