from types import SimpleNamespace

from axon import __version__
from axon.config import Config
from axon.core.event_bus import EventBus
from axon.core.states import AxonState
from axon.skills.registry import SkillRegistry
from axon.visual import web_window


class FakeMetrics:
    total = 3

    def snapshot(self):
        return {"total": self.total, "fast_path_hits": 1,
                "fallback_to_rules": 0, "backends": {}}


class FakeAi:
    metrics = FakeMetrics()

    def health(self):
        return {"active": "local", "chain": ["local"], "backends": {
            "local": {"available": True, "model": "test-model", "detail": "live"}
        }}


class FakeEntry:
    def as_dict(self):
        return {"id": "one", "content": "persisted project memory",
                "type": "project", "source": "user",
                "timestamp": "2026-06-19T20:00:00", "confidence": 0.9,
                "tags": []}


def build_bridge():
    config = Config()
    stt = SimpleNamespace(available=True, _cmd_path="models/live-model",
                          can_load=lambda: True)
    audio = SimpleNamespace(available=True, _running=True, stt=stt)
    orch = SimpleNamespace(
        ai=FakeAi(), registry=SkillRegistry().discover(),
        memory=SimpleNamespace(all_entries=lambda: [FakeEntry()]),
        audio_input=audio, tts=SimpleNamespace(available=True),
        state=AxonState.IDLE, audit_session_id="live-session",
        autonomy=None,
    )
    return web_window.Bridge(config, EventBus(), orch)


def test_panel_snapshot_uses_live_project_objects():
    data = build_bridge().panel_snapshot()

    assert data["status"]["session"] == "live-session"
    assert data["status"]["version"] == __version__
    assert data["status"]["backend"] == "local"
    assert data["status"]["model"] == "test-model"
    assert data["memory"][0]["content"] == "persisted project memory"
    assert data["ai"]["metrics"]["total"] == 3
    assert len(data["skills"]) == 6
    assert any(agent["name"] == "Speech recognition" and
               agent["status"] == "active" for agent in data["agents"])
    assert data["voice"]["tts_backend"] == "SAPI5"
    assert data["voice"]["stt_model"] == "live-model"


def test_telemetry_snapshot_has_no_synthetic_values(monkeypatch):
    bridge = build_bridge()
    bridge._last_latency_ms = 42.5
    monkeypatch.setattr(web_window.sysinfo, "read_metrics", lambda: {
        "cpu": 12.0, "memory": 34.0, "disk": 56.0, "battery": 78.0,
    })

    data = bridge.snapshot()

    assert data["cpu"] == 12.0
    assert data["mem"] == 34.0
    assert data["disk"] == 56.0
    assert data["battery"] == 78.0
    assert data["requests"] == 3
    assert data["latency"] == 42.5
    assert data["uptime_seconds"] >= 0
