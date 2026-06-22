from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine
from axon.ai.schema import Intent
from axon.skills.registry import SkillRegistry


def registry():
    return SkillRegistry().discover()


def test_native_control_commands_are_structured():
    engine = LocalIntentEngine(registry().catalogue())
    inspected = engine.interpret("inspect the active application controls", Context())
    clicked = engine.interpret("click desktop control n3 and expect Settings", Context())
    filled = engine.interpret("fill native control n2 with hello world", Context())

    assert inspected.intent.type == "desktop_inspect"
    assert clicked.intent.parameters == {"element_id": "n3", "expected": "Settings"}
    assert filled.intent.parameters == {"element_id": "n2", "text": "hello world"}


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
