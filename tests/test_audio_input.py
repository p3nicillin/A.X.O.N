"""Wake-spotter to command-recogniser handoff tests."""
from axon.config import Config
from axon.core.event_bus import Event, EventBus
from axon.perception.audio_input import AudioInput


class FakeStt:
    available = True
    has_wake = True

    def __init__(self):
        self.wake_blocks = []
        self.command_blocks = []
        self.final_text = "axon what is the time"

    def reset_wake(self):
        pass

    def reset_command(self):
        self.command_blocks = []

    def accept_wake(self, pcm):
        self.wake_blocks.append(pcm)
        return pcm == b"wake-detected"

    def wake_text(self, final):
        return "axon" if final else ""

    def accept_command(self, pcm):
        self.command_blocks.append(pcm)

    def command_final(self):
        return self.final_text, 0.95


def build_audio():
    config = Config()
    config.wake_preroll_ms = 1800
    config.vad_silence_ms = 60
    bus = EventBus()
    stt = FakeStt()
    audio = AudioInput(config, bus, stt)
    return audio, stt, bus


def test_wake_handoff_replays_audio_into_command_recognizer():
    audio, stt, _bus = build_audio()

    audio._spotter_step(b"axon-prefix", 0.03)
    audio._spotter_step(b"wake-detected", 0.03)

    assert audio._mode == "command"
    assert audio._cmd_active is True
    assert stt.command_blocks == [b"axon-prefix", b"wake-detected"]


def test_same_breath_command_finishes_from_replayed_audio():
    audio, stt, bus = build_audio()
    transcripts = []
    bus.subscribe(Event.TRANSCRIPT, lambda msg: transcripts.append(msg.payload))

    audio._spotter_step(b"axon-prefix", 0.03)
    audio._spotter_step(b"wake-detected", 0.03)
    # Two 30 ms silent blocks meet this test's 60 ms trailing-silence limit.
    audio._spotter_step(b"silence-1", 0.0)
    audio._spotter_step(b"silence-2", 0.0)

    assert transcripts == [{
        "text": "axon what is the time",
        "confidence": 0.95,
        "wake_satisfied": True,
    }]
    assert audio._mode == "wake"


def test_preroll_is_bounded_in_memory():
    audio, _stt, _bus = build_audio()
    for index in range(audio._wake_preroll.maxlen + 5):
        audio._spotter_step(f"noise-{index}".encode(), 0.0)

    assert len(audio._wake_preroll) == audio._wake_preroll.maxlen
