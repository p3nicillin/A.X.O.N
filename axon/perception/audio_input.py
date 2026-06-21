"""Microphone capture + voice-activity detection + wake spotter.

Realtime/heavy-work split: the sounddevice callback does almost nothing — it
computes an RMS level (for the reactive visuals) and drops the raw PCM onto a
bounded queue. A dedicated **worker thread** does all the expensive Vosk
decoding, so the realtime audio thread never blocks (important with the large
model).

Two operating modes:

  * **wake-spotter** (default, when a grammar recogniser is available): the
    worker runs a small state machine — listen for "AXON" with the
    grammar-biased recogniser, and on detection open a bounded command window
    that the full recogniser transcribes. Transcripts are emitted already
    "wake-satisfied" so the orchestrator does not re-gate them.
  * **legacy VAD**: every utterance is transcribed and the orchestrator applies
    the post-STT wake-word check.

If sounddevice/numpy are missing the component is inert and the system still
runs (drive it with the dev input box).
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque

from ..config import Config
from ..core.event_bus import Event, EventBus
from .stt import SttEngine

try:
    import numpy as np
    import sounddevice as sd
except Exception:  # pragma: no cover - optional
    np = None
    sd = None

_BLOCK_MS = 30


class AudioInput:
    def __init__(self, config: Config, bus: EventBus, stt: SttEngine) -> None:
        self.config = config
        self.bus = bus
        self.stt = stt
        self.available = sd is not None and np is not None
        self._stream = None
        self._running = False
        self._enabled = True              # muted while AXON speaks
        self._prev_ready = False
        self._block = int(config.sample_rate * _BLOCK_MS / 1000)
        self._pcm_q: "queue.Queue[tuple[bytes, float]]" = queue.Queue(maxsize=64)
        self._worker = threading.Thread(target=self._run, daemon=True)

        # state-machine vars (owned by the worker thread)
        self._mode = "wake"               # "wake" | "command"
        self._cmd_active = False
        self._silence = 0
        self._deadline = 0.0
        preroll_blocks = max(1, int(config.wake_preroll_ms / _BLOCK_MS))
        self._wake_preroll: deque[bytes] = deque(maxlen=preroll_blocks)
        # legacy-mode vars
        self._speaking = False

    @property
    def _silence_limit(self) -> int:
        return max(1, int(self.config.vad_silence_ms / _BLOCK_MS))

    @property
    def _use_spotter(self) -> bool:
        return self.config.use_wake_spotter and self.stt.has_wake

    def set_enabled(self, value: bool) -> None:
        self._enabled = value

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if not self.available:
            self.bus.publish(Event.LOG, {"level": "warn", "source": "audio",
                "message": "Microphone unavailable (install sounddevice+numpy)."})
            return
        self._running = True
        self._worker.start()
        self._stream = sd.RawInputStream(
            samplerate=self.config.sample_rate, blocksize=self._block,
            dtype="int16", channels=1, callback=self._on_block)
        self._stream.start()
        self.bus.publish(Event.LOG, {"level": "info", "source": "audio",
                                     "message": "Listening on microphone."})

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop(); self._stream.close()
            except Exception:
                pass

    # -- realtime callback (keep this cheap!) --------------------------------
    def _on_block(self, indata, frames, time_info, status) -> None:
        if not self._running:
            return
        pcm = bytes(indata)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(samples ** 2)) + 1e-9)
        self.bus.publish(Event.AUDIO_LEVEL, min(1.0, rms * 6.0))
        try:
            self._pcm_q.put_nowait((pcm, rms))
        except queue.Full:
            pass  # drop audio rather than back up the realtime thread

    # -- worker thread (all decoding happens here) ---------------------------
    def _run(self) -> None:
        while self._running:
            try:
                pcm, rms = self._pcm_q.get(timeout=0.1)
            except queue.Empty:
                continue

            ready = self._enabled and self.stt.available
            # reset cleanly whenever we (re)gain the floor, e.g. after TTS
            if ready and not self._prev_ready:
                self._reset_state()
            self._prev_ready = ready
            if not ready:
                continue

            if self._use_spotter:
                self._spotter_step(pcm, rms)
            else:
                self._legacy_step(pcm, rms)

    def _reset_state(self) -> None:
        self._mode = "wake"
        self._cmd_active = False
        self._silence = 0
        self._speaking = False
        self._wake_preroll.clear()
        self.stt.reset_wake()
        self.stt.reset_command()

    # -- wake-spotter state machine -----------------------------------------
    def _spotter_step(self, pcm: bytes, rms: float) -> None:
        if self._mode == "wake":
            # The wake recogniser needs several frames to decide. Retain those
            # frames so the command recogniser can start at the beginning of
            # "AXON, <command>" rather than after the decision point.
            self._wake_preroll.append(pcm)
            final = self.stt.accept_wake(pcm)
            text = self.stt.wake_text(final)
            if self.config.wake_word.lower() in text.lower().split():
                self._enter_command_mode()
            return

        # command mode
        self.stt.accept_command(pcm)
        now = time.monotonic()
        if rms >= self.config.vad_start_rms:
            self._cmd_active = True
            self._silence = 0
        elif self._cmd_active:
            self._silence += 1

        if self._cmd_active and self._silence >= self._silence_limit:
            self._finish_command()
        elif now >= self._deadline:
            self._finish_command(wake_only=not self._cmd_active)

    def _enter_command_mode(self) -> None:
        self._mode = "command"
        self._silence = 0
        self.stt.reset_command()
        for buffered_pcm in self._wake_preroll:
            self.stt.accept_command(buffered_pcm)
        # Wake detection itself proves this utterance is active. This also lets
        # VAD finalise a command already present in replay if detection arrived
        # after the speaker had finished the short phrase.
        self._cmd_active = True
        self._wake_preroll.clear()
        self._deadline = time.monotonic() + self.config.active_listen_timeout
        self.bus.publish(Event.WAKE_WORD)
        self.bus.publish(Event.SPEECH_START)

    def _finish_command(self, wake_only: bool = False) -> None:
        text, conf = self.stt.command_final()
        self._mode = "wake"
        self._cmd_active = False
        self._silence = 0
        self._wake_preroll.clear()
        self.stt.reset_wake()
        self.bus.publish(Event.SPEECH_END)
        if wake_only or not text:
            self.bus.publish(Event.TRANSCRIPT, {"text": "", "confidence": 0.0,
                             "wake_satisfied": True, "wake_only": True})
        else:
            self.bus.publish(Event.TRANSCRIPT, {"text": text, "confidence": conf,
                             "wake_satisfied": True})

    # -- legacy VAD path -----------------------------------------------------
    def _legacy_step(self, pcm: bytes, rms: float) -> None:
        if rms >= self.config.vad_start_rms:
            if not self._speaking:
                self._speaking = True
                self.stt.reset_command()
                self.bus.publish(Event.SPEECH_START)
            self._silence = 0
            self.stt.accept_command(pcm)
        elif self._speaking:
            self._silence += 1
            self.stt.accept_command(pcm)
            if self._silence >= self._silence_limit:
                self._speaking = False
                self._silence = 0
                self.bus.publish(Event.SPEECH_END)
                text, conf = self.stt.command_final()
                if text:
                    self.bus.publish(Event.TRANSCRIPT,
                                     {"text": text, "confidence": conf})
