from axon.audio.tts import (_SVS_FLAGS_ASYNC, _SVS_PURGE_BEFORE_SPEAK,
                            TtsEngine)
from axon.config import Config
from axon.core.event_bus import Event, EventBus


class FakeSapi:
    def __init__(self):
        self.spoken = []
        self.wait_calls = 0

    def Speak(self, text, flags):
        self.spoken.append((text, flags))
        self.wait_calls = 0

    def WaitUntilDone(self, _timeout):
        self.wait_calls += 1
        return self.wait_calls >= 2


def test_native_sapi_waits_for_each_utterance():
    levels = []
    bus = EventBus()
    bus.subscribe(Event.SPEAK_LEVEL, lambda msg: levels.append(msg.payload))
    tts = TtsEngine(Config(), bus)
    sapi = FakeSapi()
    tts._engine = sapi

    tts._speak_sapi("first")
    tts._speak_sapi("second")

    assert sapi.spoken == [("first", _SVS_FLAGS_ASYNC),
                           ("second", _SVS_FLAGS_ASYNC)]
    assert levels == [0.72, 0.72]


def test_native_sapi_interrupt_purges_current_utterance():
    tts = TtsEngine(Config(), EventBus())
    sapi = FakeSapi()
    tts._engine = sapi
    tts._interrupt.set()

    tts._speak_sapi("long response")

    assert sapi.spoken[-1] == (
        "", _SVS_FLAGS_ASYNC | _SVS_PURGE_BEFORE_SPEAK)
