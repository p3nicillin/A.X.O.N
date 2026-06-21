"""Hybrid Vosk wake + faster-whisper command transcription tests."""
import math
from types import SimpleNamespace

import numpy as np

from axon.config import Config
from axon.perception.stt import SttEngine


class FakeWhisper:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))
        return iter([
            SimpleNamespace(text=" what is the time", avg_logprob=-0.1),
        ]), SimpleNamespace(language_probability=0.99)


def test_whisper_command_audio_is_transient_and_transcribed():
    stt = SttEngine(Config())
    stt.command_backend = "faster-whisper"
    stt._whisper = FakeWhisper()
    stt.profile = SimpleNamespace(apply=lambda text: text)
    stt.available = True
    pcm = np.array([0, 1000, -1000, 0], dtype=np.int16).tobytes()

    stt.accept_command(pcm)
    text, confidence = stt.command_final()

    assert text == "what is the time"
    assert confidence == math.exp(-0.1)
    assert stt._command_pcm == bytearray()
    audio, options = stt._whisper.calls[0]
    assert audio.dtype == np.float32
    assert options["language"] == "en"
    assert options["beam_size"] == 5


def test_whisper_command_buffer_is_bounded_to_thirty_seconds():
    config = Config()
    stt = SttEngine(config)
    stt.command_backend = "faster-whisper"
    stt.available = True

    stt.accept_command(b"x" * (config.sample_rate * 2 * 31))

    assert len(stt._command_pcm) == config.sample_rate * 2 * 30
