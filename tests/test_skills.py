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
from axon.skills.reminders.handler import ReminderSkill
from axon.skills.browser_automation import handler as automation_handler
from axon.core.event_bus import Event, EventBus


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
    assert "ReminderSkill" in names


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


def test_system_awareness_returns_structured_local_data():
    r = reg()

    running = r.execute(Intent(type="list_running_apps"))
    network = r.execute(Intent(type="network_status"))

    assert running.ok and isinstance(running.data["apps"], list)
    assert network.ok and isinstance(network.data["connected"], bool)
    assert isinstance(network.data["ip_addresses"], list)


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


def test_browser_action_requires_and_controls_foreground_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_handler, "_send_browser_action",
                        lambda action: calls.append(action) or True)

    result = reg().execute(Intent(type="browser_action", parameters={
        "action": "reopen_tab"}))

    assert result.ok is True
    assert calls == ["reopen_tab"]


def test_managed_browser_validates_and_returns_worker_result(monkeypatch):
    calls = []

    class FakeWorker:
        def perform(self, action, parameters):
            calls.append((action, parameters))
            return {"ok": True, "title": "Example", "url": parameters["url"]}

        def stop(self):
            pass

    skill = reg().route(Intent(type="browser_navigate"))
    monkeypatch.setattr(skill, "worker", FakeWorker())
    monkeypatch.setattr(automation_handler, "_public_url", lambda url: url)

    result = skill.execute(Intent(type="browser_navigate", parameters={
        "url": "https://example.com"}))

    assert result.ok is True
    assert calls == [("navigate", {"url": "https://example.com"})]


def test_managed_browser_mutating_dom_actions_are_sensitive():
    manifest = next(m for m in reg().catalogue()
                    if m.name == "BrowserAutomationSkill")

    assert manifest.is_sensitive("browser_click") is True
    assert manifest.is_sensitive("browser_fill") is True
    assert manifest.is_sensitive("browser_read_page") is False


def test_managed_browser_accepts_grounded_ids_and_exposes_verification(monkeypatch):
    calls = []

    class FakeWorker:
        def perform(self, action, parameters):
            calls.append((action, parameters))
            return {"ok": True, "title": "Account", "url": "https://example.com",
                    "verification": {"verified": True, "reason": "page state changed"}}

        def stop(self):
            pass

    skill = reg().route(Intent(type="browser_click"))
    monkeypatch.setattr(skill, "worker", FakeWorker())
    result = skill.execute(Intent(type="browser_click", parameters={
        "element_id": "e12", "expected": "Account"}))

    assert result.ok is True
    assert result.data["verification"]["verified"] is True
    assert calls[0][1] == {"target": "", "element_id": "e12", "expected": "Account"}


def test_managed_browser_rejects_untrusted_grounded_selector(monkeypatch):
    skill = reg().route(Intent(type="browser_click"))
    result = skill.execute(Intent(type="browser_click", parameters={
        "element_id": "button:nth-child(1)"}))

    assert result.ok is False
    assert "e1" in result.summary


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


def test_screen_inspection_is_ephemeral_and_returns_local_ocr(monkeypatch):
    class FakeImage:
        size = (1920, 1080)

    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(
        ImageGrab=SimpleNamespace(grab=lambda **_kwargs: FakeImage())))
    monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace(
        image_to_string=lambda image, timeout: "Project dashboard ready"))
    monkeypatch.setattr(window_handler, "_active_window_title",
                        lambda: "AXON")

    result = screenshot_handler.SKILL.execute(Intent(type="inspect_screen"))

    assert result.ok is True
    assert result.data["text"] == "Project dashboard ready"
    assert result.data["persisted"] is False


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
    for it in ("get_active_window", "list_windows", "list_running_apps",
               "network_status", "set_timer", "set_reminder",
               "list_reminders", "cancel_reminder"):
        assert r.route(Intent(type=it)) is not None, it


def test_reminders_persist_list_cancel_and_fire(tmp_path):
    path = tmp_path / "reminders.json"
    skill = ReminderSkill(path)
    skill.manifest = next(m for m in reg().catalogue()
                          if m.name == "ReminderSkill")
    events = []
    bus = EventBus()
    bus.subscribe(Event.REMINDER_DUE, lambda message: events.append(message.payload))
    skill._bus = bus

    created = skill.execute(Intent(type="set_timer", parameters={
        "seconds": 60, "label": "tea"}))
    listed = skill.execute(Intent(type="list_reminders"))
    reloaded = ReminderSkill(path)
    reloaded.manifest = skill.manifest

    assert created.ok and listed.ok
    assert listed.data["count"] == 1
    assert reloaded.execute(Intent(type="list_reminders")).data["count"] == 1

    due = skill._fire_due(created.data["due"] + 1)
    assert due[0]["label"] == "tea"
    assert events[0]["id"] == created.data["id"]
    assert skill.execute(Intent(type="list_reminders")).data["count"] == 0

    second = skill.execute(Intent(type="set_reminder", parameters={
        "seconds": 120, "label": "stretch"}))
    cancelled = skill.execute(Intent(type="cancel_reminder", parameters={
        "identifier": second.data["id"]}))
    assert cancelled.ok


def test_reminder_rejects_unbounded_delay(tmp_path):
    skill = ReminderSkill(tmp_path / "reminders.json")
    skill.manifest = next(m for m in reg().catalogue()
                          if m.name == "ReminderSkill")

    result = skill.execute(Intent(type="set_timer", parameters={"seconds": 0}))

    assert result.ok is False


def test_reminder_background_service_stops_cleanly(tmp_path):
    skill = ReminderSkill(tmp_path / "reminders.json")
    skill.manifest = next(m for m in reg().catalogue()
                          if m.name == "ReminderSkill")

    skill.start(EventBus())
    assert skill._thread is not None and skill._thread.is_alive()
    skill.stop()

    assert skill._thread is None


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


def test_window_awareness_returns_structured_titles(monkeypatch):
    monkeypatch.setattr(window_handler.sys, "platform", "win32")
    monkeypatch.setattr(window_handler, "_active_window_title",
                        lambda: "Project - Visual Studio Code")
    monkeypatch.setattr(window_handler, "_window_titles",
                        lambda: ["Project - Visual Studio Code", "AXON"])
    skill = reg().route(Intent(type="get_active_window"))

    active = skill.execute(Intent(type="get_active_window"))
    windows = skill.execute(Intent(type="list_windows"))

    assert active.ok and active.data["title"] == "Project - Visual Studio Code"
    assert windows.ok and windows.data["count"] == 2


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
