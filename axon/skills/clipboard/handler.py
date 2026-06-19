"""ClipboardSkill — read the clipboard or replace its text.

Uses PowerShell ``Get-Clipboard`` / ``Set-Clipboard`` so the change persists
after this process exits (unlike a transient Tk clipboard). Text to set is piped
on stdin to avoid shell-quoting pitfalls. ``read_clipboard`` returns only a short
preview; it never logs full clipboard contents. Off Windows / without PowerShell
it degrades to a clean failure.
"""
from __future__ import annotations

import shutil
import subprocess

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_PREVIEW = 200  # chars surfaced from a read; full contents are never logged


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


class ClipboardSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if intent.type == "read_clipboard":
            return self._read()
        if intent.type == "set_clipboard":
            return self._set(str(intent.get("text", "")))
        return self.fail(f"Unsupported clipboard action '{intent.type}'.")

    def _read(self) -> SkillResult:
        ps = _powershell()
        if ps is None:
            return self.fail("Clipboard access requires PowerShell.",
                             speak="I can't read the clipboard on this system, sir.")
        try:
            res = subprocess.run([ps, "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                                 capture_output=True, text=True, timeout=5)
        except Exception as exc:
            return self.fail(f"Could not read the clipboard: {exc}",
                             speak="I couldn't read the clipboard, sir.")
        text = (res.stdout or "").rstrip("\r\n")
        if not text:
            return self.ok("The clipboard is empty.",
                           speak="The clipboard is empty, sir.", length=0)
        preview = text[:_PREVIEW]
        return self.ok(f"Clipboard: {preview}",
                       speak=f"Your clipboard says: {preview}",
                       length=len(text))

    def _set(self, text: str) -> SkillResult:
        if not text.strip():
            return self.fail("No text was provided to copy.",
                             speak="There's nothing to copy, sir.")
        ps = _powershell()
        if ps is None:
            return self.fail("Clipboard access requires PowerShell.",
                             speak="I can't set the clipboard on this system, sir.")
        try:
            res = subprocess.run([ps, "-NoProfile", "-Command",
                                  "$input | Set-Clipboard"],
                                 input=text, capture_output=True, text=True,
                                 timeout=5)
            if res.returncode != 0:
                return self.fail("Setting the clipboard failed.",
                                 speak="I couldn't set the clipboard, sir.")
        except Exception as exc:
            return self.fail(f"Could not set the clipboard: {exc}",
                             speak="I couldn't set the clipboard, sir.")
        return self.ok("Copied to the clipboard.",
                       speak="Copied to your clipboard, sir.", length=len(text))


SKILL = ClipboardSkill()
