"""Speech-to-text via Vosk (offline, streaming), with two recognisers.

  * a **full** recogniser for transcribing commands, and
  * a **grammar-biased** recogniser constrained to just the wake word + "[unk]",
    used by the always-on wake spotter so "AXON" is detected reliably even
    though it is an uncommon proper noun.

The model is large (~1.8 GB) and takes ~20 s to load, so loading is deferred:
construct the engine instantly, then call :meth:`load` (or :meth:`load_async`)
in the background. Until then ``available`` is False and the rest of the system
runs without STT.
"""
from __future__ import annotations

import json
import math
import threading
import time
import wave
from pathlib import Path

from ..config import DATA_DIR, Config
from .speech_profile import SpeechProfile

VOICE_SAMPLES_DIR = DATA_DIR / "voice_samples"

try:
    import vosk
    vosk.SetLogLevel(-1)
except Exception:  # pragma: no cover - optional
    vosk = None

try:
    import numpy as np
except Exception:  # pragma: no cover - required by normal audio install
    np = None

try:
    from faster_whisper import WhisperModel
except Exception:  # pragma: no cover - optional high-accuracy backend
    WhisperModel = None


def _model_dirs() -> list[Path]:
    from ..config import MODELS_DIR
    return [c for c in MODELS_DIR.glob("*")
            if c.is_dir() and (c / "conf").exists()]


def _find_command_model(config: Config) -> str | None:
    if config.stt_model_path and Path(config.stt_model_path).exists():
        return config.stt_model_path
    dirs = _model_dirs()
    if not dirs:
        return None
    # prefer the larger, more accurate model (name without "small")
    full = [d for d in dirs if "small" not in d.name.lower()]
    return str((full or dirs)[0])


def _find_wake_model(config: Config, command_path: str | None) -> str | None:
    """A small, grammar-capable model is ideal for the wake spotter. Large
    static-graph models don't support runtime grammars, so fall back to the
    command model only as a last resort."""
    if config.stt_wake_model_path and Path(config.stt_wake_model_path).exists():
        return config.stt_wake_model_path
    small = [d for d in _model_dirs() if "small" in d.name.lower()]
    if small:
        return str(small[0])
    return command_path


class SttEngine:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.available = False
        self.reason = ""
        self._cmd_model = None
        self._wake_model = None
        self._cmd = None
        self._wake = None
        self._whisper = None
        self._command_pcm = bytearray()
        self.profile = SpeechProfile()
        self._cmd_path = _find_command_model(config) if vosk else None
        self._wake_path = _find_wake_model(config, self._cmd_path) if vosk else None
        preference = str(getattr(config, "stt_engine", "auto")).lower()
        self.command_backend = (
            "faster-whisper" if preference != "vosk" and WhisperModel is not None
            and np is not None else "vosk")
        self.model_name = (config.stt_whisper_model
                           if self.command_backend == "faster-whisper"
                           else Path(self._cmd_path or "").name)
        if self.command_backend == "vosk" and vosk is None:
            self.reason = "no speech recognizer installed"
        elif self.command_backend == "vosk" and self._cmd_path is None:
            self.reason = "no Vosk model found in models/"

    # -- loading -------------------------------------------------------------
    def can_load(self) -> bool:
        return (self.command_backend == "faster-whisper"
                or (vosk is not None and self._cmd_path is not None))

    def load(self) -> bool:
        if not self.can_load():
            return False
        if self.command_backend == "faster-whisper":
            try:
                from ..config import MODELS_DIR
                self._whisper = WhisperModel(
                    self.config.stt_whisper_model,
                    device=self.config.stt_whisper_device,
                    compute_type=self.config.stt_whisper_compute_type,
                    download_root=str(MODELS_DIR / "whisper"),
                )
            except Exception as exc:
                if vosk is None or self._cmd_path is None:
                    raise
                print(f"[stt] faster-whisper unavailable, using Vosk: {exc}")
                self.command_backend = "vosk"
                self.model_name = Path(self._cmd_path).name
        if self.command_backend == "vosk":
            self._cmd_model = vosk.Model(self._cmd_path)
        if self._wake_path and vosk is not None:
            self._wake_model = (
                self._cmd_model if self._wake_path == self._cmd_path
                and self._cmd_model is not None else vosk.Model(self._wake_path))
        self._cmd = self._new_cmd()
        self._wake = self._new_wake()
        self.available = True
        return True

    def load_async(self, on_ready) -> None:
        def run():
            try:
                ok = self.load()
                on_ready(ok, "" if ok else self.reason)
            except Exception as exc:
                self.reason = f"load failed: {exc}"
                on_ready(False, self.reason)
        threading.Thread(target=run, daemon=True).start()

    def _new_cmd(self):
        if self.command_backend == "faster-whisper":
            return None
        rec = vosk.KaldiRecognizer(self._cmd_model, self.config.sample_rate)
        rec.SetWords(True)
        return rec

    def _new_wake(self):
        if self._wake_model is None:
            return None
        try:
            grammar = json.dumps([self.config.wake_word.lower(), "[unk]"])
            return vosk.KaldiRecognizer(self._wake_model, self.config.sample_rate,
                                        grammar)
        except Exception as exc:
            print(f"[stt] grammar wake recogniser unavailable: {exc}")
            return None

    @property
    def has_wake(self) -> bool:
        return self._wake is not None

    # -- command recogniser --------------------------------------------------
    def reset_command(self) -> None:
        if self.available:
            self._command_pcm.clear()
            self._cmd = self._new_cmd()

    def accept_command(self, pcm: bytes) -> None:
        if not self.available:
            return
        if self.command_backend == "faster-whisper":
            limit = int(self.config.sample_rate * 2 * 30)
            remaining = max(0, limit - len(self._command_pcm))
            self._command_pcm.extend(pcm[:remaining])
        else:
            self._cmd.AcceptWaveform(pcm)

    def command_final(self) -> tuple[str, float]:
        if not self.available:
            return "", 0.0
        if self.command_backend == "faster-whisper":
            if not self._command_pcm:
                return "", 0.0
            audio = (np.frombuffer(bytes(self._command_pcm), dtype=np.int16)
                     .astype(np.float32) / 32768.0)
            segments, info = self._whisper.transcribe(
                audio, language="en", beam_size=5, vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=self._initial_prompt(),
            )
            segments = list(segments)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            confidences = [math.exp(min(0.0, segment.avg_logprob))
                           for segment in segments]
            conf = (sum(confidences) / len(confidences) if confidences else
                    float(getattr(info, "language_probability", 0.0) or 0.0))
            self._save_voice_sample(bytes(self._command_pcm), text, conf)
            self.reset_command()
            return self.profile.apply(text), max(0.0, min(1.0, conf))
        res = json.loads(self._cmd.FinalResult())
        text = res.get("text", "").strip()
        words = res.get("result", [])
        conf = (sum(w.get("conf", 1.0) for w in words) / len(words)) if words else (
            1.0 if text else 0.0)
        self.reset_command()
        return self.profile.apply(text), conf

    def _initial_prompt(self) -> str:
        base = ("AXON voice assistant. What is the time? What is the weather? "
                "Open Notepad. System status. Calculate. Set a reminder.")
        snapshot = getattr(self.profile, "snapshot", lambda: [])()
        expected = [str(item.get("expected", "")) for item in snapshot[:30]
                    if isinstance(item, dict) and item.get("expected")]
        return base + (" Personal command phrases: " + ". ".join(expected)
                       if expected else "")

    def _save_voice_sample(self, pcm: bytes, transcript: str,
                           confidence: float) -> None:
        if not getattr(self.config, "voice_sample_collection", False) or not pcm:
            return
        try:
            VOICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            stem = f"sample-{stamp}-{time.time_ns() % 1_000_000:06d}"
            path = VOICE_SAMPLES_DIR / f"{stem}.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(self.config.sample_rate)
                audio.writeframes(pcm)
            metadata = VOICE_SAMPLES_DIR / f"{stem}.json"
            metadata.write_text(json.dumps({
                "transcript": transcript[:500],
                "confidence": round(float(confidence), 4),
                "sample_rate": self.config.sample_rate,
            }, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def clear_voice_samples() -> int:
        count = 0
        if VOICE_SAMPLES_DIR.exists():
            for path in VOICE_SAMPLES_DIR.glob("sample-*.*"):
                try:
                    path.unlink()
                    count += 1
                except OSError:
                    pass
        return count

    # -- wake recogniser -----------------------------------------------------
    def reset_wake(self) -> None:
        if self.available and self._wake is not None:
            self._wake = self._new_wake()

    def accept_wake(self, pcm: bytes) -> bool:
        """Feed audio; returns True when an utterance boundary is reached."""
        if self.available and self._wake is not None:
            return self._wake.AcceptWaveform(pcm)
        return False

    def wake_text(self, final: bool) -> str:
        if not (self.available and self._wake is not None):
            return ""
        raw = self._wake.Result() if final else self._wake.PartialResult()
        data = json.loads(raw)
        return (data.get("text") or data.get("partial") or "").strip()

    # -- legacy aliases (kept for compatibility) -----------------------------
    def reset(self) -> None:
        self.reset_command()

    def accept(self, pcm: bytes) -> None:
        self.accept_command(pcm)

    def final(self) -> tuple[str, float]:
        return self.command_final()
