"""MediaControlSkill — transport control via the OS media keys.

Pure-stdlib: synthesises the virtual media keys with ``ctypes`` on Windows. No
process is launched and no audio device is touched directly. Off Windows (or if
the key API is unavailable) it degrades to a clean failure rather than raising.
"""
from __future__ import annotations

import sys

from ...ai.schema import Intent, SkillResult
from ..base import Skill

# Virtual-key codes for the media transport keys.
_VK = {
    "play_pause": 0xB3,      # VK_MEDIA_PLAY_PAUSE
    "next_track": 0xB0,      # VK_MEDIA_NEXT_TRACK
    "previous_track": 0xB1,  # VK_MEDIA_PREV_TRACK
}
_SPOKEN = {
    "play_pause": "Toggled playback",
    "next_track": "Skipped to the next track",
    "previous_track": "Went to the previous track",
}


def _tap_key(vk: int) -> None:
    """Press and release a single virtual key (Windows only)."""
    import ctypes

    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


class MediaControlSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        vk = _VK.get(intent.type)
        if vk is None:
            return self.fail(f"Unsupported media action '{intent.type}'.")
        if sys.platform != "win32":
            return self.fail("Media keys are only available on Windows.",
                             speak="I can't control media on this system, sir.")
        try:
            _tap_key(vk)
        except Exception as exc:
            return self.fail(f"Could not send the media key: {exc}",
                             speak="I couldn't control media, sir.")
        phrase = _SPOKEN.get(intent.type, "Done")
        return self.ok(phrase + ".", speak=phrase + ", sir.", action=intent.type)


SKILL = MediaControlSkill()
