"""VolumeSkill — system volume up/down/mute via the OS volume keys.

Pure-stdlib ``ctypes`` on Windows; no audio library required. ``volume_up`` and
``volume_down`` accept an optional ``steps`` count, clamped to a safe range so a
single command can never slam the volume to an extreme. Off Windows it degrades
to a clean failure.
"""
from __future__ import annotations

import sys

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_VK_VOLUME_MUTE = 0xAD

_MIN_STEPS, _MAX_STEPS, _DEFAULT_STEPS = 1, 10, 2


def _tap_key(vk: int, times: int = 1) -> None:
    import ctypes

    KEYEVENTF_KEYUP = 0x0002
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _coerce_steps(raw) -> int:
    try:
        steps = int(raw)
    except (TypeError, ValueError):
        steps = _DEFAULT_STEPS
    return max(_MIN_STEPS, min(_MAX_STEPS, steps))


class VolumeSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if intent.type not in ("volume_up", "volume_down", "mute_toggle"):
            return self.fail(f"Unsupported volume action '{intent.type}'.")
        if sys.platform != "win32":
            return self.fail("Volume control is only available on Windows.",
                             speak="I can't change the volume on this system, sir.")
        try:
            if intent.type == "mute_toggle":
                _tap_key(_VK_VOLUME_MUTE)
                return self.ok("Toggled mute.", speak="Muted, sir.",
                               action="mute_toggle")
            steps = _coerce_steps(intent.get("steps", _DEFAULT_STEPS))
            vk = _VK_VOLUME_UP if intent.type == "volume_up" else _VK_VOLUME_DOWN
            _tap_key(vk, steps)
            verb = "up" if intent.type == "volume_up" else "down"
            return self.ok(f"Turned volume {verb} ({steps} steps).",
                           speak=f"Volume {verb}, sir.",
                           action=intent.type, steps=steps)
        except Exception as exc:
            return self.fail(f"Could not change the volume: {exc}",
                             speak="I couldn't change the volume, sir.")


SKILL = VolumeSkill()
