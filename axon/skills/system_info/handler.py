"""SystemInfoSkill — read-only machine telemetry (also feeds the HUD gauges)."""
from __future__ import annotations

import socket

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
        # interval=None returns a meaningless 0.0 on the first call made from
        # each worker thread. Take a short real sample so spoken reports and
        # the HUD agree with Task Manager.
        "cpu": psutil.cpu_percent(interval=0.1),
        "memory": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "battery": bat,
    }


class SystemInfoSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if psutil is None:
            return self.fail("System telemetry requires the 'psutil' package.")
        if intent.type == "list_running_apps":
            return self._running_apps()
        if intent.type == "network_status":
            return self._network_status()
        m = read_metrics()
        parts = [f"CPU at {m['cpu']:.1f} percent",
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
        summary = (f"CPU {m['cpu']:.1f}% | MEM {m['memory']:.0f}% | "
                   f"DISK {m['disk']:.0f}% | listener active")
        return self.ok(summary, speak=spoken, **m)

    def _running_apps(self) -> SkillResult:
        names: dict[str, str] = {}
        try:
            for process in psutil.process_iter(["name"]):
                name = str(process.info.get("name") or "").strip()
                if name and name.casefold() not in {
                        "system", "system idle process", "registry"}:
                    names.setdefault(name.casefold(), name)
        except (psutil.Error, OSError) as exc:
            return self.fail(f"Could not inspect running applications: {exc}")
        apps = sorted(names.values(), key=str.casefold)
        visible = apps[:30]
        if not visible:
            return self.ok("No user applications were detected.", apps=[])
        summary = ", ".join(visible)
        if len(apps) > len(visible):
            summary += f" (+{len(apps) - len(visible)} more)"
        spoken_names = ", ".join(visible[:10])
        return self.ok(summary,
                       speak=f"Running applications include {spoken_names}, sir.",
                       apps=visible, total=len(apps), truncated=len(apps) > 30)

    def _network_status(self) -> SkillResult:
        try:
            stats = psutil.net_if_stats()
            addresses = psutil.net_if_addrs()
            active = []
            ips = []
            for name, interface in stats.items():
                if not interface.isup:
                    continue
                found = [address.address for address in addresses.get(name, [])
                         if address.family == socket.AF_INET
                         and not address.address.startswith("127.")]
                if found:
                    active.append(name)
                    ips.extend(found)
            counters = psutil.net_io_counters()
        except (psutil.Error, OSError) as exc:
            return self.fail(f"Could not inspect the network: {exc}")
        connected = bool(active and ips)
        if connected:
            spoken = (f"Your local network is connected on {active[0]}. "
                      f"The local IP address is {ips[0]}, sir.")
            summary = f"Connected | {active[0]} | {ips[0]}"
        else:
            spoken = "I can't detect an active local network connection, sir."
            summary = "No active local network detected."
        return self.ok(summary, speak=spoken, connected=connected,
                       interfaces=active, ip_addresses=ips,
                       bytes_sent=int(counters.bytes_sent),
                       bytes_received=int(counters.bytes_recv))


SKILL = SystemInfoSkill()
