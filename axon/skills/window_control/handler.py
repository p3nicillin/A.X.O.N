"""WindowControlSkill — focus and govern foreground/named windows.

Pure-stdlib ``ctypes`` (user32) on Windows. Close sends a graceful WM_CLOSE and
is confirmation-gated; it never force-terminates the owning process.
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
    "focus_window": "Focused the window",
    "close_window": "Asked the window to close",
}


def _resolve_window(title: str = "") -> int:
    import ctypes

    user32 = ctypes.windll.user32
    if not title.strip():
        return int(user32.GetForegroundWindow() or 0)
    needle = title.strip().casefold()
    matches = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                       ctypes.c_void_p)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if needle in buffer.value.casefold():
                matches.append(int(hwnd))
                return False
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return matches[0] if matches else 0


def _apply_window_action(hwnd: int, action: str) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    if action == "focus_window":
        user32.ShowWindow(hwnd, 9)
        return bool(user32.SetForegroundWindow(hwnd))
    if action == "close_window":
        return bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE
    user32.ShowWindow(hwnd, _SW[action])
    return True


class WindowControlSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if intent.type not in {*_SW, "focus_window", "close_window"}:
            return self.fail(f"Unsupported window action '{intent.type}'.")
        if sys.platform != "win32":
            return self.fail("Window control is only available on Windows.",
                             speak="I can't manage windows on this system, sir.")
        try:
            title = str(intent.get("title", "")).strip()
            hwnd = _resolve_window(title)
            if not hwnd:
                target = f" matching {title}" if title else ""
                return self.fail(f"No window{target} was found.",
                                 speak="I couldn't find that window, sir.")
            if not _apply_window_action(hwnd, intent.type):
                return self.fail("Windows rejected that window action.")
        except Exception as exc:
            return self.fail(f"Could not change the window: {exc}",
                             speak="I couldn't manage that window, sir.")
        phrase = _SPOKEN.get(intent.type, "Done")
        return self.ok(phrase + ".", speak=phrase + ", sir.",
                       action=intent.type, title=str(intent.get("title", "")))


SKILL = WindowControlSkill()
