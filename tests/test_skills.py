"""Contract + behaviour tests for the skill engine."""
from pathlib import Path
from types import SimpleNamespace
import sys

from axon.ai.schema import Intent
from axon.skills.registry import SkillRegistry
from axon.skills.file_system import handler as file_handler
from axon.skills.window_control import handler as window_handler
from axon.skills.screenshot import handler as screenshot_handler
from axon.skills.browser import handler as browser_handler
from axon.skills.app_launcher import handler as app_handler


def reg():
    return SkillRegistry().discover()


def test_all_skills_discovered():
    names = {m.name for m in reg().catalogue()}
    assert {"TimeDateSkill", "AppLauncherSkill", "SystemInfoSkill",
            "WebSearchSkill", "NotesSkill", "FileSystemSkill",
            "MediaControlSkill", "VolumeSkill", "WindowControlSkill",
            "ClipboardSkill", "ScreenshotSkill", "KeyboardSkill"} <= names
    assert "WeatherSkill" in names
    assert "CalculatorSkill" in names
    assert "BrowserSkill" in names


def test_manifest_contract():
    for m in reg().catalogue():
        assert m.name and m.version and m.intents
        assert isinstance(m.sensitive, bool)
        assert set(m.sensitive_intents) <= set(m.intents)


def test_router_resolves_declared_intents():
    r = reg()
    for m in r.catalogue():
        for it in m.intents:
            assert r.route(Intent(type=it)) is not None, it


def test_unknown_intent_is_handled_gracefully():
    res = reg().execute(Intent(type="does_not_exist"))
    assert res.ok is False and "No skill" in res.summary


def test_registry_rejects_parameters_not_declared_by_manifest():
    res = reg().execute(Intent(type="get_time", parameters={"timezone": "UTC"}))
    assert res.ok is False
    assert "unsupported parameter" in res.summary


def test_time_and_date_execute():
    r = reg()
    assert r.execute(Intent(type="get_time")).ok
    assert r.execute(Intent(type="get_date")).ok


def test_app_launcher_rejects_empty_name():
    res = reg().execute(Intent(type="open_app", parameters={"app": "   "}))
    assert res.ok is False


def test_app_launcher_opens_non_aliased_app(monkeypatch):
    calls = []
    monkeypatch.setattr(app_handler.subprocess, "Popen",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    res = reg().execute(Intent(type="open_app", parameters={"app": "explorer"}))
    assert res.ok is True
    assert calls


def test_app_launcher_rejects_malformed_multiword_name_without_shell(
        monkeypatch):
    calls = []
    monkeypatch.setattr(app_handler.subprocess, "Popen",
                        lambda *args, **kwargs: calls.append((args, kwargs)))

    result = reg().execute(Intent(type="open_app", parameters={
        "app": "youtube on google chrome"}))

    assert result.ok is False
    assert calls == []


def test_browser_skill_opens_known_site_in_requested_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_handler, "_browser_executable",
                        lambda browser: "C:/Chrome/chrome.exe")
    monkeypatch.setattr(browser_handler.subprocess, "Popen",
                        lambda args, **kwargs: calls.append((args, kwargs)))

    result = reg().execute(Intent(type="open_website", parameters={
        "site": "YouTube", "browser": "Google Chrome"}))

    assert result.ok is True
    assert calls[0][0] == ["C:/Chrome/chrome.exe", "https://www.youtube.com/"]


def test_browser_skill_rejects_non_url_command_text():
    result = reg().execute(Intent(type="open_website", parameters={
        "site": "youtube on google chrome && whoami"}))

    assert result.ok is False


def test_browser_skill_opens_private_site_with_correct_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_handler, "_browser_executable",
                        lambda browser: "C:/Chrome/chrome.exe")
    monkeypatch.setattr(browser_handler.subprocess, "Popen",
                        lambda args, **kwargs: calls.append((args, kwargs)))

    result = reg().execute(Intent(type="open_website", parameters={
        "site": "YouTube", "private": True}))

    assert result.ok is True
    assert calls[0][0] == ["C:/Chrome/chrome.exe", "--incognito",
                           "https://www.youtube.com/"]
    assert result.data["private"] is True


def test_browser_skill_searches_with_encoded_query(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_handler, "_browser_executable",
                        lambda browser: "C:/Firefox/firefox.exe")
    monkeypatch.setattr(browser_handler.subprocess, "Popen",
                        lambda args, **kwargs: calls.append((args, kwargs)))

    result = reg().execute(Intent(type="search_browser", parameters={
        "query": "AXON voice commands", "browser": "Firefox",
        "private": True}))

    assert result.ok is True
    assert calls[0][0] == ["C:/Firefox/firefox.exe", "-private-window",
                           "https://www.google.com/search?q=AXON+voice+commands"]


def test_browser_skill_rejects_invalid_private_value():
    result = reg().execute(Intent(type="open_browser", parameters={
        "private": "sometimes"}))

    assert result.ok is False
    assert "boolean" in result.summary


def test_filesystem_is_sandboxed():
    # path traversal must be refused
    res = reg().execute(Intent(type="list_files",
                               parameters={"path": "../../.."}))
    assert res.ok is False


def test_filesystem_skill_is_marked_sensitive():
    fs = next(m for m in reg().catalogue() if m.name == "FileSystemSkill")
    assert fs.is_sensitive("read_file") is False
    assert fs.is_sensitive("write_file") is True
    assert fs.is_sensitive("delete_path") is True


def test_workspace_file_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(file_handler, "WORKSPACE", tmp_path.resolve())
    r = reg()

    written = r.execute(Intent(type="write_file", parameters={
        "path": "draft.txt", "text": "hello", "append": False}))
    appended = r.execute(Intent(type="write_file", parameters={
        "path": "draft.txt", "text": " world", "append": True}))
    read = r.execute(Intent(type="read_file", parameters={"path": "draft.txt"}))
    moved = r.execute(Intent(type="move_path", parameters={
        "source": "draft.txt", "destination": "final.txt"}))
    deleted = r.execute(Intent(type="delete_path", parameters={
        "path": "final.txt"}))

    assert written.ok and appended.ok and read.ok and moved.ok and deleted.ok
    assert read.data["text"] == "hello world"
    assert not (tmp_path / "draft.txt").exists()
    assert not (tmp_path / "final.txt").exists()


def test_workspace_mutations_cannot_escape_or_delete_nonempty_folder(
        tmp_path, monkeypatch):
    monkeypatch.setattr(file_handler, "WORKSPACE", tmp_path.resolve())
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "keep.txt").write_text("keep", encoding="utf-8")
    r = reg()

    escaped = r.execute(Intent(type="write_file", parameters={
        "path": "../outside.txt", "text": "no", "append": False}))
    nonempty = r.execute(Intent(type="delete_path", parameters={
        "path": "folder"}))

    assert escaped.ok is False
    assert nonempty.ok is False
    assert (folder / "keep.txt").exists()


def test_clipboard_set_rejects_empty_text():
    res = reg().execute(Intent(type="set_clipboard", parameters={"text": "  "}))
    assert res.ok is False


def test_per_intent_sensitivity_distinguishes_clipboard_read_and_write():
    clipboard = next(m for m in reg().catalogue()
                     if m.name == "ClipboardSkill")
    assert clipboard.is_sensitive("read_clipboard") is False
    assert clipboard.is_sensitive("set_clipboard") is True


def test_screenshot_rejects_paths_and_unknown_parameters():
    r = reg()
    escaped = r.execute(Intent(type="capture_screenshot",
                               parameters={"filename": "../outside.png"}))
    unknown = r.execute(Intent(type="capture_screenshot",
                               parameters={"format": "jpg"}))
    assert escaped.ok is False
    assert unknown.ok is False


def test_screenshot_writes_png_inside_its_sandbox(tmp_path, monkeypatch):
    class FakeImage:
        def save(self, path, format):
            assert format == "PNG"
            Path(path).write_bytes(b"png")

    sandbox = tmp_path / "workspace" / "screenshots"
    monkeypatch.setattr(screenshot_handler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(screenshot_handler, "SCREENSHOT_DIR", sandbox)
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(
        ImageGrab=SimpleNamespace(grab=lambda **_kwargs: FakeImage())))
    result = screenshot_handler.SKILL.execute(Intent(
        type="capture_screenshot", parameters={"filename": "safe-name"}))
    assert result.ok is True
    assert (sandbox / "safe-name.png").read_bytes() == b"png"


def test_keyboard_rejects_empty_and_unknown_input_without_side_effects():
    r = reg()
    empty = r.execute(Intent(type="type_text", parameters={"text": ""}))
    unknown = r.execute(Intent(type="send_keystroke",
                               parameters={"keys": "ctrl+c", "delay": 1}))
    assert empty.ok is False
    assert unknown.ok is False


def test_new_control_intents_route():
    # Routing only — no OS side effects are triggered here.
    r = reg()
    for it in ("play_pause", "next_track", "previous_track", "volume_up",
               "volume_down", "mute_toggle", "minimize_window",
               "maximize_window", "restore_window", "read_clipboard",
               "set_clipboard"):
        assert r.route(Intent(type=it)) is not None, it
    for it in ("capture_screenshot", "type_text", "send_keystroke"):
        assert r.route(Intent(type=it)) is not None, it
    for it in ("focus_window", "close_window"):
        assert r.route(Intent(type=it)) is not None, it


def test_named_window_focus_uses_resolved_handle(monkeypatch):
    calls = []
    monkeypatch.setattr(window_handler.sys, "platform", "win32")
    monkeypatch.setattr(window_handler, "_resolve_window",
                        lambda title: 42 if title == "Spotify" else 0)
    monkeypatch.setattr(window_handler, "_apply_window_action",
                        lambda hwnd, action: calls.append((hwnd, action)) or True)
    skill = reg().route(Intent(type="focus_window"))

    result = skill.execute(Intent(type="focus_window",
                                  parameters={"title": "Spotify"}))

    assert result.ok is True
    assert calls == [(42, "focus_window")]


def test_window_close_is_individually_sensitive():
    manifest = next(m for m in reg().catalogue()
                    if m.name == "WindowControlSkill")

    assert manifest.is_sensitive("focus_window") is False
    assert manifest.is_sensitive("close_window") is True


def test_notes_roundtrip():
    r = reg()
    r.execute(Intent(type="clear_notes"))
    r.execute(Intent(type="add_note", parameters={"text": "buy milk"}))
    res = r.execute(Intent(type="read_notes"))
    assert res.ok and "buy milk" in (res.summary + (res.speak or ""))
    r.execute(Intent(type="clear_notes"))
