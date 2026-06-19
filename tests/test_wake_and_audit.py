"""Wake-word gating + tamper-evident audit trail."""
import json

from axon.config import Config
from axon.core.event_bus import Event, EventBus
from axon.enterprise.audit import AuditLogger
from axon.perception.wake_word import WakeWord


def test_wake_word_required_blocks_without_keyword():
    cfg = Config(); cfg.require_wake_word = True
    w = WakeWord(cfg)
    heard, _ = w.strip("what time is it")
    assert heard is False


def test_wake_word_strips_keyword_from_command():
    cfg = Config(); cfg.require_wake_word = True
    w = WakeWord(cfg)
    heard, command = w.strip("AXON what time is it")
    assert heard is True
    assert "AXON" not in command.lower()
    assert command.strip() == "what time is it"


def test_wake_word_optional_passes_through():
    cfg = Config(); cfg.require_wake_word = False
    w = WakeWord(cfg)
    heard, _ = w.strip("anything at all")
    assert heard is True


def test_wake_word_tolerates_common_mishearings():
    cfg = Config(); cfg.require_wake_word = True
    w = WakeWord(cfg)
    # the exact mishearings seen in the live logs must still activate
    for utterance in ("this what time is it", "javis open notepad",
                      "jervis system status"):
        heard, command = w.strip(utterance)
        assert heard is True, utterance
        assert "time" in command or "notepad" in command or "status" in command


def test_wake_word_fuzzy_rejects_unrelated_words():
    cfg = Config(); cfg.require_wake_word = True
    w = WakeWord(cfg)
    heard, _ = w.strip("computer what time is it")
    assert heard is False


def test_audit_chain_is_tamper_evident():
    cfg = Config(); cfg.audit_enabled = True
    bus = EventBus()
    audit = AuditLogger(cfg, bus)
    bus.publish(Event.TRANSCRIPT, {"text": "AXON hello", "confidence": 1.0})
    bus.publish(Event.INTENT, {"intent": {"type": "get_time"}})

    lines = audit._audit_path.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(l) for l in lines]
    assert len(records) >= 2
    # each record's seq_prev must equal the previous record's hash (chain)
    for prev, cur in zip(records, records[1:]):
        assert cur["seq_prev"] == prev["hash"]
