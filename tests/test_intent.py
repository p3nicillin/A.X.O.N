"""Tests for the offline intent engine and the AI->intent contract."""
from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine
from axon.ai.schema import IntentPacket, command_type_for
from axon.skills.registry import SkillRegistry


def engine():
    return LocalIntentEngine(SkillRegistry().discover().catalogue())


def parse(text):
    return engine().interpret(text, Context())


def test_time_and_date_intents():
    assert parse("what time is it").intent.type == "get_time"
    assert parse("what is today's date").intent.type == "get_date"


def test_open_app_extracts_name():
    p = parse("open notepad")
    assert p.intent.type == "open_app"
    assert p.intent.parameters["app"] == "notepad"


def test_note_preserves_casing():
    p = parse("note that I need to call Mom")
    assert p.intent.type == "add_note"
    assert "Mom" in p.intent.parameters["text"]


def test_search_strips_filler():
    p = parse("search for the speed of light")
    assert p.intent.type == "web_search"
    assert p.intent.parameters["query"] == "the speed of light"


def test_chat_fallback_is_not_a_skill():
    p = parse("hello AXON")
    assert p.intent.type == "chat"
    assert p.needs_skill is False


def test_unknown_command_classified_as_unknown():
    p = parse("teleport me to the moon")
    assert p.intent.type == "unknown"
    assert p.needs_skill is False
    assert p.command_type == "UNKNOWN"


def test_command_type_mapping_covers_six_categories():
    assert command_type_for("get_time") == "TIME_DATE"
    assert command_type_for("open_app") == "APP_CONTROL"
    assert command_type_for("system_info") == "SYSTEM_STATUS"
    assert command_type_for("web_search") == "WEB_SEARCH"
    assert command_type_for("add_note") == "NOTES"
    assert command_type_for("find_file") == "FILE_ACCESS"
    assert command_type_for("nonsense") == "UNKNOWN"


def test_classification_record_shape():
    p = parse("what time is it")
    rec = p.classification(requires_confirmation=False)
    assert set(rec) == {"command_type", "confidence", "requires_confirmation",
                        "requires_tool"}
    assert rec["command_type"] == "TIME_DATE"
    assert rec["requires_tool"] is True


def test_packet_from_dict_marks_skill_need():
    p = IntentPacket.from_dict(
        {"thought": "t", "intent": {"type": "get_time", "parameters": {}},
         "response_text": "ok"})
    assert p.needs_skill is True
    chat = IntentPacket.from_dict(
        {"thought": "t", "intent": {"type": "chat"}, "response_text": "hi"})
    assert chat.needs_skill is False
