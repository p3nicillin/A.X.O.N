"""AppLauncherSkill — open/close desktop apps.

``ALIASES`` are friendly-name shortcuts (e.g. "files" -> explorer). Names not in
the map fall through to the OS launcher unchanged, so any app on PATH / App Paths
can be opened by name. Only an empty name is refused.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from ...ai.schema import Intent, SkillResult
from ..base import Skill

# friendly name -> launch target. Targets are either:
#   - a bare command found on PATH / App Paths (start handles these)
#   - an absolute path
ALIASES: dict[str, str] = {
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "explorer": "explorer",
    "files": "explorer",
    "file explorer": "explorer",
    "settings": "ms-settings:",
    "terminal": "wt",
    "powershell": "powershell",
    "cmd": "cmd",
    "task manager": "taskmgr",
    "paint": "mspaint",
    "spotify": "spotify",
    "vscode": "code",
    "vs code": "code",
    "code": "code",
}

# friendly name -> process image name for closing
CLOSE_TARGETS: dict[str, str] = {
    "chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe",
    "notepad": "notepad.exe", "calculator": "Calculator.exe", "calc": "Calculator.exe",
    "spotify": "spotify.exe", "vscode": "Code.exe", "code": "Code.exe",
    "paint": "mspaint.exe", "terminal": "WindowsTerminal.exe",
}


class AppLauncherSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        name = str(intent.get("app", "")).strip().lower()
        if not name:
            return self.fail("No application name was provided.")

        if intent.type == "close_app":
            return self._close(name)
        return self._open(name)

    def _open(self, name: str) -> SkillResult:
        # Known alias -> mapped target; otherwise launch the spoken name as-is.
        target = ALIASES.get(name, name)
        try:
            if target.endswith(":"):  # ms-settings: style URI
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                # `start` resolves App Paths and PATH; shell-safe single arg
                subprocess.Popen(["cmd", "/c", "start", "", target],
                                 close_fds=True)
            return self.ok(f"Opening {name}.", speak=f"Opening {name}, sir.",
                           app=name)
        except Exception as exc:
            return self.fail(f"Could not open {name}: {exc}",
                             speak="I couldn't open that, sir.")

    def _close(self, name: str) -> SkillResult:
        # Known target image, else derive one from the spoken name.
        image = CLOSE_TARGETS.get(name)
        if image is None:
            image = name if name.lower().endswith(".exe") else f"{name}.exe"
        if shutil.which("taskkill") is None:
            return self.fail("taskkill is unavailable on this system.",
                             speak="I couldn't close that, sir.")
        try:
            res = subprocess.run(["taskkill", "/IM", image, "/F"],
                                 capture_output=True, text=True)
            if res.returncode == 0:
                return self.ok(f"Closed {name}.", speak=f"Closed {name}, sir.",
                               app=name)
            return self.fail(f"{name} did not appear to be running.",
                             speak=f"{name} doesn't appear to be running, sir.")
        except Exception as exc:
            return self.fail(f"Could not close {name}: {exc}",
                             speak="I couldn't close that, sir.")


SKILL = AppLauncherSkill()
