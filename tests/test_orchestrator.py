"""Pipeline tests: destructive-action confirmation + §11 command logging."""
import time

from jarvis.config import Config
from jarvis.core.event_bus import Event, EventBus
from jarvis.core.orchestrator import Orchestrator
from jarvis.skills.registry import SkillRegistry


class FakeTts:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        pass


def build():
    cfg = Config()                 # require_wake_word False, confirm_sensitive True
    bus = EventBus()
    logs = []
    bus.subscribe(Event.COMMAND_LOG, lambda m: logs.append(m.payload))
    tts = FakeTts()
    orch = Orchestrator(cfg, bus, SkillRegistry().discover(), tts, None)
    return orch, tts, logs


def wait(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_non_destructive_executes_without_confirmation():
    orch, tts, logs = build()
    orch.submit_text("what time is it")
    assert wait(lambda: logs)
    assert logs[-1]["command_type"] == "TIME_DATE"
    assert logs[-1]["skill_used"] == "TimeDateSkill"
    assert logs[-1]["success"] is True
    assert orch._pending is None


def test_destructive_requires_confirmation_then_executes():
    orch, tts, logs = build()
    orch.submit_text("clear my notes")
    # should be gated, awaiting confirmation, nothing logged yet
    assert wait(lambda: orch._pending is not None)
    assert any("clear all of your notes" in s.lower() for s in tts.spoken)
    assert logs == []
    # confirm
    orch.submit_text("yes")
    assert wait(lambda: logs)
    assert logs[-1]["command_type"] == "NOTES"
    assert logs[-1]["success"] is True


def test_destructive_can_be_cancelled():
    orch, tts, logs = build()
    orch.submit_text("clear my notes")
    assert wait(lambda: orch._pending is not None)
    orch.submit_text("no")
    assert wait(lambda: logs)
    assert logs[-1]["success"] is False
    assert any("leave it" in s.lower() for s in tts.spoken)


def test_unknown_command_is_not_executed():
    orch, tts, logs = build()
    orch.submit_text("teleport me to mars")
    assert wait(lambda: logs)
    assert logs[-1]["command_type"] == "UNKNOWN"
    assert logs[-1]["skill_used"] == "none"


def test_wake_satisfied_transcript_executes_without_regating():
    orch, tts, logs = build()
    orch.config.require_wake_word = True   # even so, spotter pre-gated it
    orch.bus.publish(Event.TRANSCRIPT,
                     {"text": "what time is it", "wake_satisfied": True})
    assert wait(lambda: logs)
    assert logs[-1]["command_type"] == "TIME_DATE"
    assert logs[-1]["success"] is True


def test_wake_only_transcript_acknowledges():
    orch, tts, logs = build()
    orch.bus.publish(Event.TRANSCRIPT,
                     {"text": "", "wake_satisfied": True, "wake_only": True})
    assert wait(lambda: tts.spoken)
    assert any("yes" in s.lower() for s in tts.spoken)


def test_voice_transcript_without_wake_is_ignored_when_required():
    orch, tts, logs = build()
    orch.wake.required = True   # WakeWord captures this at construction
    orch.bus.publish(Event.TRANSCRIPT, {"text": "what time is it"})
    time.sleep(0.3)
    assert logs == []          # never reached a skill
