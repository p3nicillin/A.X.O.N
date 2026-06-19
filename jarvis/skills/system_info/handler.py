"""SystemInfoSkill — read-only machine telemetry (also feeds the HUD gauges)."""
from __future__ import annotations

from ...ai.schema import Intent, SkillResult
from ..base import Skill

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


def read_metrics() -> dict[str, float | None]:
    """Shared helper: the HUD polls this directly for live gauges."""
    if psutil is None:
        return {"cpu": None, "memory": None, "disk": None, "battery": None}
    try:
        battery = psutil.sensors_battery()
        bat = battery.percent if battery else None
    except Exception:
        bat = None
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "memory": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "battery": bat,
    }


class SystemInfoSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if psutil is None:
            return self.fail("System telemetry requires the 'psutil' package.")
        m = read_metrics()
        parts = [f"CPU at {m['cpu']:.0f} percent",
                 f"memory at {m['memory']:.0f} percent",
                 f"disk at {m['disk']:.0f} percent"]
        if m["battery"] is not None:
            parts.append(f"battery at {m['battery']:.0f} percent")
        # §5: report raw metrics + a stable/strained verdict, nothing more.
        strained = any(v is not None and v > 88 for v in
                       (m["cpu"], m["memory"], m["disk"]))
        verdict = "Systems are under load, sir." if strained else \
                  "All systems are stable, sir."
        spoken = ", ".join(parts) + ". " + verdict
        summary = (f"CPU {m['cpu']:.0f}% | MEM {m['memory']:.0f}% | "
                   f"DISK {m['disk']:.0f}% | listener active")
        return self.ok(summary, speak=spoken, **m)


SKILL = SystemInfoSkill()
