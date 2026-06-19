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
import threading
from pathlib import Path

from ..config import Config

try:
    import vosk
    vosk.SetLogLevel(-1)
except Exception:  # pragma: no cover - optional
    vosk = None


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
        self._cmd_path = _find_command_model(config) if vosk else None
        self._wake_path = _find_wake_model(config, self._cmd_path) if vosk else None
        if vosk is None:
            self.reason = "vosk not installed"
        elif self._cmd_path is None:
            self.reason = "no Vosk model found in models/"

    # -- loading -------------------------------------------------------------
    def can_load(self) -> bool:
        return vosk is not None and self._cmd_path is not None

    def load(self) -> bool:
        if not self.can_load():
            return False
        self._cmd_model = vosk.Model(self._cmd_path)
        # reuse the same Model object if wake and command paths coincide
        self._wake_model = (self._cmd_model if self._wake_path == self._cmd_path
                            else vosk.Model(self._wake_path)) if self._wake_path \
            else None
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
            self._cmd = self._new_cmd()

    def accept_command(self, pcm: bytes) -> None:
        if self.available:
            self._cmd.AcceptWaveform(pcm)

    def command_final(self) -> tuple[str, float]:
        if not self.available:
            return "", 0.0
        res = json.loads(self._cmd.FinalResult())
        text = res.get("text", "").strip()
        words = res.get("result", [])
        conf = (sum(w.get("conf", 1.0) for w in words) / len(words)) if words else (
            1.0 if text else 0.0)
        self.reset_command()
        return text, conf

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
