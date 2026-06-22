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


def test_website_and_requested_browser_are_separate_from_app_name():
    p = parse("Open YouTube on Google Chrome.")

    assert p.intent.type == "open_website"
    assert p.intent.parameters == {
        "site": "YouTube", "browser": "Google Chrome"}


def test_website_private_mode_is_parsed_without_becoming_an_app_name():
    p = parse("Open YouTube in an incognito browser.")

    assert p.intent.type == "open_website"
    assert p.intent.parameters == {"site": "YouTube", "private": True}


def test_private_browser_and_browser_search_intents_parse():
    opened = parse("Open an incognito Chrome window.")
    searched = parse("Search Google for AXON voice commands with Firefox.")

    assert opened.intent.type == "open_browser"
    assert opened.intent.parameters == {
        "browser": "Chrome", "private": True}
    assert searched.intent.type == "search_browser"
    assert searched.intent.parameters == {
        "query": "AXON voice commands", "browser": "Firefox"}


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


def test_media_and_volume_and_window_intents_parse():
    assert parse("pause the music").intent.type == "play_pause"
    assert parse("next track").intent.type == "next_track"
    assert parse("previous track").intent.type == "previous_track"
    assert parse("volume up").intent.type == "volume_up"
    assert parse("turn it down").intent.type == "volume_down"
    assert parse("mute").intent.type == "mute_toggle"
    assert parse("minimize the window").intent.type == "minimize_window"
    assert parse("maximize this window").intent.type == "maximize_window"


def test_clipboard_intents_parse():
    p = parse("copy Hello World to the clipboard")
    assert p.intent.type == "set_clipboard"
    assert p.intent.parameters["text"] == "Hello World"
    assert parse("read my clipboard").intent.type == "read_clipboard"


def test_new_intents_have_command_categories():
    assert command_type_for("play_pause") == "MEDIA_CONTROL"
    assert command_type_for("volume_up") == "VOLUME_CONTROL"
    assert command_type_for("minimize_window") == "WINDOW_CONTROL"
    assert command_type_for("read_clipboard") == "CLIPBOARD"
    assert command_type_for("open_website") == "WEB_NAVIGATION"
    assert command_type_for("search_browser") == "WEB_NAVIGATION"
    assert command_type_for("open_browser") == "WEB_NAVIGATION"


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
