"""Contract + behaviour tests for the skill engine."""
from axon.ai.schema import Intent
from axon.skills.registry import SkillRegistry


def reg():
    return SkillRegistry().discover()


def test_all_skills_discovered():
    names = {m.name for m in reg().catalogue()}
    assert {"TimeDateSkill", "AppLauncherSkill", "SystemInfoSkill",
            "WebSearchSkill", "NotesSkill", "FileSystemSkill"} <= names


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


def test_app_launcher_rejects_non_whitelisted():
    res = reg().execute(Intent(type="open_app", parameters={"app": "evil.exe"}))
    assert res.ok is False


def test_filesystem_is_sandboxed():
    # path traversal must be refused
    res = reg().execute(Intent(type="list_files",
                               parameters={"path": "../../.."}))
    assert res.ok is False


def test_filesystem_skill_is_marked_sensitive():
    fs = next(m for m in reg().catalogue() if m.name == "FileSystemSkill")
    assert fs.sensitive is True


def test_notes_roundtrip():
    r = reg()
    r.execute(Intent(type="clear_notes"))
    r.execute(Intent(type="add_note", parameters={"text": "buy milk"}))
    res = r.execute(Intent(type="read_notes"))
    assert res.ok and "buy milk" in (res.summary + (res.speak or ""))
    r.execute(Intent(type="clear_notes"))
