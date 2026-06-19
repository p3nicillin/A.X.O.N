"""Interruptible text-to-speech.

A single worker thread owns the speech engine (native Windows SAPI5, with
pyttsx3 as a fallback). The
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

try:
    import win32com.client as win32_client
except Exception:
    win32_client = None


_SVS_FLAGS_ASYNC = 1
_SVS_PURGE_BEFORE_SPEAK = 2


class TtsEngine:
    def __init__(self, config: Config, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self.available = win32_client is not None or pyttsx3 is not None
        self.backend_name = "SAPI5" if win32_client is not None else (
            "pyttsx3" if pyttsx3 is not None else "unavailable")
        self.selected_voice = config.tts_voice or "system default"
        self.voice_names: list[str] = []
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
            if self._engine is not None and self.backend_name == "pyttsx3":
                try:
                    self._engine.stop()
                except Exception:
                    pass

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def reconfigure(self, *, voice: str, rate: int) -> None:
        """Update plain config only; the worker applies it on the next utterance.

        The SAPI COM object stays owned by the worker apartment.
        """
        self.config.tts_voice = voice
        self.config.tts_rate = rate

    # -- worker thread -------------------------------------------------------
    def _run(self) -> None:
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass
        if win32_client is not None:
            try:
                self._engine = win32_client.Dispatch("SAPI.SpVoice")
                self.backend_name = "SAPI5"
                self._configure_sapi()
            except Exception as exc:
                print(f"[tts] SAPI5 init failed, trying pyttsx3: {exc}")
                self._engine = None
        if self._engine is None and pyttsx3 is not None:
            try:
                self._engine = pyttsx3.init()
                self.backend_name = "pyttsx3"
                self._engine.setProperty("rate", self.config.tts_rate)
                self._select_pyttsx3_voice()
                self._engine.connect("started-word", self._on_word)
            except Exception as exc:
                print(f"[tts] fallback init failed: {exc}")
                self._engine = None
        self.available = self._engine is not None

        while True:
            text = self._q.get()
            if text is None:
                return
            self._interrupt.clear()
            self._speaking.set()
            self.bus.publish(Event.SPEAK_START, {"text": text})
            try:
                if self.available:
                    if self.backend_name == "SAPI5":
                        self._configure_sapi()
                        self._speak_sapi(text)
                    else:
                        self._engine.setProperty("rate", self.config.tts_rate)
                        self._select_pyttsx3_voice()
                        self._engine.say(text)
                        self._engine.runAndWait()
                else:
                    self._simulate(text)
            except Exception as exc:
                print(f"[tts] speak error: {exc}")
            finally:
                self._speaking.clear()
                self.bus.publish(Event.SPEAK_END)

    def _configure_sapi(self) -> None:
        # SAPI rate is -10..10; 200 wpm is approximately its neutral rate.
        self._engine.Rate = max(-10, min(10, round((self.config.tts_rate - 200) / 20)))
        want = self.config.tts_voice.strip().lower()
        voices = self._engine.GetVoices()
        self.voice_names = [voices.Item(i).GetDescription()
                            for i in range(voices.Count)]
        selected = self._engine.Voice
        for i in range(voices.Count):
            voice = voices.Item(i)
            if want and want in voice.GetDescription().lower():
                selected = voice
                break
        self._engine.Voice = selected
        self.selected_voice = selected.GetDescription()

    def _speak_sapi(self, text: str) -> None:
        self._engine.Speak(text, _SVS_FLAGS_ASYNC)
        while not self._engine.WaitUntilDone(50):
            if self._interrupt.is_set():
                self._engine.Speak("", _SVS_FLAGS_ASYNC | _SVS_PURGE_BEFORE_SPEAK)
                self._engine.WaitUntilDone(1000)
                break
            if pythoncom is not None:
                pythoncom.PumpWaitingMessages()
            self.bus.publish(Event.SPEAK_LEVEL, 0.72)

    def _select_pyttsx3_voice(self) -> None:
        want = self.config.tts_voice.strip().lower()
        voices = self._engine.getProperty("voices")
        self.voice_names = [v.name for v in voices]
        selected = None
        for v in voices:
            if want in v.name.lower():
                self._engine.setProperty("voice", v.id)
                selected = v
                break
        if selected is None and voices:
            selected = voices[0]
        if selected is not None:
            self.selected_voice = selected.name

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
