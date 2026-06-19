import json

import pytest

from axon import config as config_module
from axon.config import Config


def test_user_settings_persist_atomically_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "user_settings.json"
    monkeypatch.setattr(config_module, "USER_SETTINGS_PATH", path)
    cfg = Config()

    saved = cfg.update_user_settings({
        "tts_voice": "Hazel", "tts_rate": 210,
        "address_term": "ma'am", "wake_ack_phrase": "Ready.",
        "require_wake_word": True,
    })

    assert saved["tts_rate"] == 210
    assert json.loads(path.read_text(encoding="utf-8"))["address_term"] == "ma'am"
    assert not path.with_suffix(".json.tmp").exists()
    reloaded = Config.load()
    assert reloaded.tts_rate == 210
    assert reloaded.address_term == "ma'am"


def test_environment_override_is_reported_as_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "USER_SETTINGS_PATH",
                        tmp_path / "user_settings.json")
    monkeypatch.setenv("AXON_TTS_RATE", "240")
    cfg = Config.load()

    assert cfg.tts_rate == 240
    assert "tts_rate" in cfg.user_settings_snapshot()["locked"]
    with pytest.raises(ValueError, match="environment-locked"):
        cfg.update_user_settings({"tts_rate": 200})


@pytest.mark.parametrize("change", [
    {"tts_rate": 20}, {"require_wake_word": "yes"},
    {"ai_engine": "untrusted"}, {"secret_key": "no"},
])
def test_user_settings_reject_invalid_values(tmp_path, monkeypatch, change):
    path = tmp_path / "user_settings.json"
    monkeypatch.setattr(config_module, "USER_SETTINGS_PATH", path)
    with pytest.raises(ValueError):
        Config().update_user_settings(change)
    assert not path.exists()
