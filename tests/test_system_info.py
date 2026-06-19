from types import SimpleNamespace

from axon.ai.schema import Intent
from axon.skills.system_info import handler


class FakePsutil:
    def __init__(self):
        self.cpu_intervals = []

    def cpu_percent(self, interval=None):
        self.cpu_intervals.append(interval)
        return 12.3

    @staticmethod
    def virtual_memory():
        return SimpleNamespace(percent=45.6)

    @staticmethod
    def disk_usage(_path):
        return SimpleNamespace(percent=67.8)

    @staticmethod
    def sensors_battery():
        return None


def test_metrics_take_a_real_cpu_sample(monkeypatch):
    psutil = FakePsutil()
    monkeypatch.setattr(handler, "psutil", psutil)

    metrics = handler.read_metrics()

    assert psutil.cpu_intervals == [0.1]
    assert metrics == {"cpu": 12.3, "memory": 45.6, "disk": 67.8,
                       "battery": None}


def test_status_report_preserves_cpu_precision(monkeypatch):
    monkeypatch.setattr(handler, "read_metrics", lambda: {
        "cpu": 2.4, "memory": 45.6, "disk": 67.8, "battery": None,
    })

    result = handler.SKILL.execute(Intent(type="system_info"))

    assert "CPU at 2.4 percent" in result.speak
    assert "CPU 2.4%" in result.summary
