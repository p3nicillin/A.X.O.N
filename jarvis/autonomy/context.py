"""§16.1 context-awareness engine — strictly READ-ONLY.

Samples ambient machine state into a :class:`ContextSnapshot`: CPU/memory load,
the set of running application processes, how long the user has been idle, and
the foreground window title. Everything here only *observes*; nothing is
launched, closed, or modified (§16 "read-only awareness", §1 safety).

Idle time and the active window use Windows APIs via ctypes; on other platforms
(or if a call fails) those fields degrade gracefully to neutral values.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None

_IS_WIN = sys.platform == "win32"


@dataclass
class ContextSnapshot:
    timestamp: str
    cpu: float = 0.0
    memory: float = 0.0
    idle_seconds: float = 0.0
    active_window: str = ""
    apps: frozenset[str] = field(default_factory=frozenset)

    @property
    def app_count(self) -> int:
        return len(self.apps)

    def as_dict(self) -> dict:
        return {"timestamp": self.timestamp, "cpu": round(self.cpu, 1),
                "memory": round(self.memory, 1),
                "idle_seconds": round(self.idle_seconds, 1),
                "active_window": self.active_window, "app_count": self.app_count}


def _idle_seconds() -> float:
    if not _IS_WIN:
        return 0.0
    try:
        import ctypes

        class _LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = _LII()
        lii.cbSize = ctypes.sizeof(_LII)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 0.0


def _active_window() -> str:
    if not _IS_WIN:
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def _running_apps() -> frozenset[str]:
    if psutil is None:
        return frozenset()
    names: set[str] = set()
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").strip()
            if name:
                names.add(name.lower())
    except Exception:
        pass
    return frozenset(names)


class ContextSensor:
    """Produces read-only snapshots of the ambient environment."""

    def __init__(self) -> None:
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)   # prime the counter
            except Exception:
                pass

    def snapshot(self) -> ContextSnapshot:
        cpu = mem = 0.0
        if psutil is not None:
            try:
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory().percent
            except Exception:
                pass
        return ContextSnapshot(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            cpu=cpu, memory=mem,
            idle_seconds=_idle_seconds(),
            active_window=_active_window(),
            apps=_running_apps())
