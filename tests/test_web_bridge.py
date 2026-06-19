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
        audio_input=audio, tts=SimpleNamespace(
            available=True, backend_name="SAPI5", selected_voice="Test Voice"),
        state=AxonState.IDLE, audit_session_id="live-session",
        autonomy=None, crash_reporter=SimpleNamespace(summary=lambda: {
            "enabled": True, "count": 2,
            "last": {"timestamp": "2026-06-19T20:00:00"},
        }),
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
    assert data["voice"]["voice"] == "Test Voice"
    assert data["voice"]["stt_model"] == "live-model"
    assert data["diagnostics"]["crash_reports"] == 2


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


def test_audit_history_pages_newest_first_and_skips_bad_lines(tmp_path,
                                                               monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "audit-20260618.jsonl").write_text(
        '{"ts":"2026-06-18T10:00:00","session":"old","type":"session_start"}\n',
        encoding="utf-8")
    (logs / "audit-20260619.jsonl").write_text(
        '{"ts":"2026-06-19T10:00:00","session":"new","type":"state_changed","payload":"idle"}\n'
        'truncated garbage\n'
        '{"ts":"2026-06-19T11:00:00","session":"new","type":"transcript","payload":{"text":"private words"}}\n',
        encoding="utf-8")
    monkeypatch.setattr(web_window, "DATA_DIR", tmp_path)
    bridge = build_bridge()

    first = bridge.audit_history(0, 2)
    second = bridge.audit_history(first["next_offset"], 2)

    assert first["total"] == 3
    assert [r["type"] for r in first["records"]] == ["transcript", "state_changed"]
    assert "private words" not in first["records"][0]["summary"]
    assert second["records"][0]["session"] == "old"
    assert second["next_offset"] is None
