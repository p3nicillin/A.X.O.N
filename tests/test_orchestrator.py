"""Pipeline tests: destructive-action confirmation + §11 command logging."""
import time

from axon.config import Config
from axon.core.event_bus import Event, EventBus
from axon.core.orchestrator import Orchestrator
from axon.skills.registry import SkillRegistry


class FakeTts:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        pass


def build():
    cfg = Config()                 # require_wake_word False, confirm_sensitive True
    cfg.ai.engine = "rules"        # keep unit tests hermetic when Ollama is installed
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


def test_due_reminder_is_spoken_inside_axon():
    orch, tts, _logs = build()

    orch.bus.publish(Event.REMINDER_DUE, {"label": "check the oven"})

    assert any("reminder: check the oven" in text.lower()
               for text in tts.spoken)


def test_per_intent_sensitive_clipboard_write_requires_confirmation():
    orch, tts, logs = build()
    orch.submit_text("copy Hello World to the clipboard")
    assert wait(lambda: orch._pending is not None)
    assert logs == []
    orch.submit_text("no")
    assert wait(lambda: logs)
    assert logs[-1]["intent"] == "set_clipboard"
    assert logs[-1]["success"] is False


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


# -- Phase 5 §A: multi-step agentic execution --------------------------------
def test_compound_command_runs_multiple_steps():
    orch, tts, logs = build()
    orch.submit_text("what time is it and then read my notes")
    assert wait(lambda: len(logs) >= 2)
    intents = [entry["intent"] for entry in logs]
    assert "get_time" in intents and "read_notes" in intents


def test_plan_steps_share_one_correlation_id():
    orch, tts, logs = build()
    orch.submit_text("what time is it and then read my notes")
    assert wait(lambda: len(logs) >= 2)
    corrs = {entry["correlation"] for entry in logs}
    assert len(corrs) == 1 and "" not in corrs


def test_simple_command_has_no_plan_correlation():
    orch, tts, logs = build()
    orch.submit_text("what time is it")
    assert wait(lambda: logs)
    assert logs[-1]["correlation"] == ""     # single-step fast path untouched


def test_plan_pauses_for_sensitive_step_then_completes():
    orch, tts, logs = build()
    orch.submit_text("read my notes and then clear my notes")
    # first step runs, then the destructive step gates the plan
    assert wait(lambda: orch._pending is not None)
    assert any("clear all of your notes" in s.lower() for s in tts.spoken)
    assert [e["intent"] for e in logs] == ["read_notes"]
    orch.submit_text("yes")
    assert wait(lambda: len(logs) >= 2)
    assert [e["intent"] for e in logs] == ["read_notes", "clear_notes"]
    assert all(e["success"] for e in logs)
    assert len({e["correlation"] for e in logs}) == 1


def test_plan_aborts_when_sensitive_step_cancelled():
    orch, tts, logs = build()
    orch.submit_text("read my notes and then clear my notes")
    assert wait(lambda: orch._pending is not None)
    orch.submit_text("no")
    assert wait(lambda: len(logs) >= 2)
    by_intent = {e["intent"]: e for e in logs}
    assert by_intent["read_notes"]["success"] is True
    assert by_intent["clear_notes"]["success"] is False
