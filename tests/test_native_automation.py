from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine
from axon.ai.schema import Intent
from axon.skills.registry import SkillRegistry
from axon.skills.native_automation.handler import AccessibilityBridge


def registry():
    return SkillRegistry().discover()


def test_native_control_commands_are_structured():
    engine = LocalIntentEngine(registry().catalogue())
    inspected = engine.interpret("inspect the active application controls", Context())
    clicked = engine.interpret("click desktop control u3 and expect Settings", Context())
    filled = engine.interpret("fill native control u2 with hello world", Context())

    assert inspected.intent.type == "desktop_inspect"
    assert clicked.intent.parameters == {"element_id": "u3", "expected": "Settings"}
    assert filled.intent.parameters == {"element_id": "u2", "text": "hello world"}


def test_native_mutations_are_confirmation_gated():
    manifest = next(m for m in registry().catalogue()
                    if m.name == "NativeAutomationSkill")

    assert manifest.is_sensitive("desktop_inspect") is False
    assert manifest.is_sensitive("desktop_click") is True
    assert manifest.is_sensitive("desktop_fill") is True


def test_native_skill_validates_ids_and_returns_verification(monkeypatch):
    calls = []

    class FakeWorker:
        def perform(self, action, parameters):
            calls.append((action, parameters))
            return {"ok": True, "element_id": "n4", "characters": 5,
                    "verification": {"verified": True,
                                     "reason": "control value matches requested text"}}

        def stop(self):
            pass

    skill = registry().route(Intent("desktop_fill"))
    monkeypatch.setattr(skill, "worker", FakeWorker())
    result = skill.execute(Intent("desktop_fill", {
        "element_id": "n4", "text": "hello"}))

    assert result.ok is True
    assert result.data["verification"]["verified"] is True
    assert calls == [("fill", {"element_id": "n4", "text": "hello"})]
    assert skill.execute(Intent("desktop_click", {
        "element_id": "button:first-child"})).ok is False


def test_release_workflow_does_not_reference_secrets_in_conditions():
    text = open(".github/workflows/release.yml", encoding="utf-8").read()

    assert "if: ${{ secrets." not in text
    assert "if: env.AXON_SIGN_CERT_BASE64 != ''" in text


def test_accessibility_bridge_hides_runtime_ids_and_reuses_snapshot(tmp_path,
                                                                    monkeypatch):
    helper = tmp_path / "helper.ps1"
    helper.write_text("# test", encoding="utf-8")
    requests = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    responses = iter([
        Completed('{"ok":true,"root_id":"42.1","count":1,"elements":'
                  '[{"id":"u1","runtime_id":"42.2","role":"Button"}]}'),
        Completed('{"ok":true,"verification":{"verified":true}}'),
    ])

    def fake_run(*args, **kwargs):
        requests.append(kwargs["input"])
        return next(responses)

    monkeypatch.setattr("axon.skills.native_automation.handler.subprocess.run",
                        fake_run)
    bridge = AccessibilityBridge(helper_path=helper)
    bridge.powershell = "powershell.exe"

    inspected = bridge.inspect()
    acted = bridge.act("click", {"element_id": "u1", "expected": "Done"})

    assert inspected["elements"] == [{"id": "u1", "role": "Button"}]
    assert "runtime_id" not in str(inspected)
    assert acted["ok"] is True
    assert '"root_id": "42.1"' in requests[1]
    assert '"target_id": "42.2"' in requests[1]


def test_accessibility_bridge_contains_helper_timeout(tmp_path, monkeypatch):
    import subprocess

    helper = tmp_path / "helper.ps1"
    helper.write_text("# test", encoding="utf-8")
    monkeypatch.setattr(
        "axon.skills.native_automation.handler.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("powershell", 3)))
    bridge = AccessibilityBridge(timeout=3, helper_path=helper)
    bridge.powershell = "powershell.exe"

    result = bridge.inspect()

    assert result["ok"] is False
    assert "timed out" in result["error"]
