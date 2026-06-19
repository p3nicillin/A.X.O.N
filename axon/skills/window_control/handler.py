"""WindowControlSkill — minimise/maximise/restore the foreground window.

Pure-stdlib ``ctypes`` (user32) on Windows. It only changes window *show state*;
it never closes a window (that stays out of scope to avoid data loss) and never
launches anything. Off Windows it degrades to a clean failure.
"""
from __future__ import annotations

import sys

from ...ai.schema import Intent, SkillResult
from ..base import Skill

# ShowWindow nCmdShow values.
_SW = {
    "minimize_window": 6,   # SW_MINIMIZE
    "maximize_window": 3,   # SW_MAXIMIZE
    "restore_window": 9,    # SW_RESTORE
}
_SPOKEN = {
    "minimize_window": "Minimised the window",
    "maximize_window": "Maximised the window",
    "restore_window": "Restored the window",
}


def _show_foreground(cmd: int) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, cmd)
    return True


class WindowControlSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        cmd = _SW.get(intent.type)
        if cmd is None:
            return self.fail(f"Unsupported window action '{intent.type}'.")
        if sys.platform != "win32":
            return self.fail("Window control is only available on Windows.",
                             speak="I can't manage windows on this system, sir.")
        try:
            if not _show_foreground(cmd):
                return self.fail("No foreground window to act on.",
                                 speak="There's no active window, sir.")
        except Exception as exc:
            return self.fail(f"Could not change the window: {exc}",
                             speak="I couldn't manage that window, sir.")
        phrase = _SPOKEN.get(intent.type, "Done")
        return self.ok(phrase + ".", speak=phrase + ", sir.", action=intent.type)


SKILL = WindowControlSkill()
