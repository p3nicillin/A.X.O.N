from types import SimpleNamespace

from axon.perception import vision
from axon.ai.schema import Intent
from axon.skills.base import SkillManifest
from axon.skills.screenshot.handler import ScreenshotSkill


class FakeImage:
    size = (1280, 720)

    def save(self, buffer, format):
        assert format == "PNG"
        buffer.write(b"png")


def test_vision_client_rejects_nonlocal_endpoint():
    client = vision.LocalVisionClient("https://vision.example.com", "model")

    result = client.analyze(FakeImage())

    assert result["ok"] is False
    assert "local" in result["error"]


def test_local_vision_analysis_is_ephemeral(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"message": {"content": "A settings dialog is visible."}})
    calls = []
    monkeypatch.setattr(vision, "requests", SimpleNamespace(
        post=lambda url, **kwargs: calls.append((url, kwargs)) or response))
    client = vision.LocalVisionClient("http://127.0.0.1:11434", "vision-test")

    result = client.analyze(FakeImage(), "Describe errors")

    assert result["ok"] is True
    assert result["analysis"] == "A settings dialog is visible."
    assert result["image_persisted"] is False
    assert calls[0][0].endswith("/api/chat")
    assert calls[0][1]["json"]["messages"][0]["images"]


def test_screen_skill_uses_configured_local_vision_and_keeps_image_ephemeral(
        monkeypatch):
    import sys
    image = FakeImage()
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(
        ImageGrab=SimpleNamespace(grab=lambda **_kwargs: image)))
    monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace(
        image_to_string=lambda *_args, **_kwargs: ""))
    prompts = []
    monkeypatch.setattr(vision.LocalVisionClient, "analyze",
                        lambda self, _image, prompt: prompts.append(prompt) or {
                            "ok": True, "analysis": "A browser error is visible.",
                            "model": "vision-test"})
    skill = ScreenshotSkill()
    skill.manifest = SkillManifest(
        "ScreenshotSkill", "test", "test", ["inspect_screen"])
    skill.configure(SimpleNamespace(
        vision_enabled=True, vision_endpoint="http://127.0.0.1:11434",
        vision_model="vision-test", vision_timeout=5))

    result = skill.execute(Intent(type="inspect_screen", parameters={
        "prompt": "Find the error"}))

    assert result.ok is True
    assert result.data["analysis"] == "A browser error is visible."
    assert result.data["persisted"] is False
    assert prompts == ["Find the error"]
