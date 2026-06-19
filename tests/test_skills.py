"""Contract + behaviour tests for the skill engine."""
from axon.ai.schema import Intent
from axon.skills.registry import SkillRegistry


def reg():
    return SkillRegistry().discover()


def test_all_skills_discovered():
    names = {m.name for m in reg().catalogue()}
    assert {"TimeDateSkill", "AppLauncherSkill", "SystemInfoSkill",
            "WebSearchSkill", "NotesSkill", "FileSystemSkill",
            "MediaControlSkill", "VolumeSkill", "WindowControlSkill",
            "ClipboardSkill"} <= names


def test_manifest_contract():
    for m in reg().catalogue():
        assert m.name and m.version and m.intents
        assert isinstance(m.sensitive, bool)


def test_router_resolves_declared_intents():
    r = reg()
    for m in r.catalogue():
        for it in m.intents:
            assert r.route(Intent(type=it)) is not None, it


def test_unknown_intent_is_handled_gracefully():
    res = reg().execute(Intent(type="does_not_exist"))
    assert res.ok is False and "No skill" in res.summary


def test_time_and_date_execute():
    r = reg()
    assert r.execute(Intent(type="get_time")).ok
    assert r.execute(Intent(type="get_date")).ok


def test_app_launcher_rejects_empty_name():
    res = reg().execute(Intent(type="open_app", parameters={"app": "   "}))
    assert res.ok is False


def test_app_launcher_opens_non_aliased_app():
    # Whitelist removed: any named app passes through to the OS launcher.
    res = reg().execute(Intent(type="open_app", parameters={"app": "explorer"}))
    assert res.ok is True


def test_filesystem_is_sandboxed():
    # path traversal must be refused
    res = reg().execute(Intent(type="list_files",
                               parameters={"path": "../../.."}))
    assert res.ok is False


def test_filesystem_skill_is_marked_sensitive():
    fs = next(m for m in reg().catalogue() if m.name == "FileSystemSkill")
    assert fs.sensitive is True


def test_clipboard_set_rejects_empty_text():
    res = reg().execute(Intent(type="set_clipboard", parameters={"text": "  "}))
    assert res.ok is False


def test_new_control_intents_route():
    # Routing only — no OS side effects are triggered here.
    r = reg()
    for it in ("play_pause", "next_track", "previous_track", "volume_up",
               "volume_down", "mute_toggle", "minimize_window",
               "maximize_window", "restore_window", "read_clipboard",
               "set_clipboard"):
        assert r.route(Intent(type=it)) is not None, it


def test_notes_roundtrip():
    r = reg()
    r.execute(Intent(type="clear_notes"))
    r.execute(Intent(type="add_note", parameters={"text": "buy milk"}))
    res = r.execute(Intent(type="read_notes"))
    assert res.ok and "buy milk" in (res.summary + (res.speak or ""))
    r.execute(Intent(type="clear_notes"))
