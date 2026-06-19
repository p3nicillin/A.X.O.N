from axon import config as config_module
from axon.config import Config
from axon.core.event_bus import EventBus
from axon.core.orchestrator import Orchestrator
from axon.skills.registry import SkillRegistry


class FakeTts:
    available = True
    backend_name = "test"
    selected_voice = "test"
    voice_names = ["test"]

    def __init__(self):
        self.configured = None

    def speak(self, _text):
        pass

    def stop(self):
        pass

    def reconfigure(self, **settings):
        self.configured = settings


def build(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "USER_SETTINGS_PATH",
                        tmp_path / "user_settings.json")
    cfg = Config()
    cfg.ai.engine = "rules"
    tts = FakeTts()
    orch = Orchestrator(cfg, EventBus(), SkillRegistry().discover(), tts, None)
    return orch, tts


def test_voice_settings_apply_live_and_persist(tmp_path, monkeypatch):
    orch, tts = build(tmp_path, monkeypatch)
    result = orch.update_user_settings({
        "tts_voice": "Test Voice", "tts_rate": 205,
        "address_term": "commander", "wake_ack_phrase": "Ready.",
        "require_wake_word": True,
    })

    assert result["ok"] is True
    assert tts.configured == {"voice": "Test Voice", "rate": 205}
    assert orch.wake.required is True


def test_rules_backend_switch_rebuilds_router(tmp_path, monkeypatch):
    orch, _ = build(tmp_path, monkeypatch)
    old = orch.ai

    result = orch.switch_ai_engine("rules")

    assert result["ok"] is True
    assert orch.ai is not old
    assert orch.ai.health()["active"] == "rules"


def test_backend_switch_rejects_busy_and_disabled_cloud(tmp_path, monkeypatch):
    orch, _ = build(tmp_path, monkeypatch)
    orch._busy.acquire()
    try:
        assert orch.switch_ai_engine("rules")["ok"] is False
    finally:
        orch._busy.release()
    result = orch.switch_ai_engine("cloud")
    assert result["ok"] is False
    assert "disabled" in result["error"]
