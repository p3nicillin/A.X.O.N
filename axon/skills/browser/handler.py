"""Open validated websites without treating phrases as executable names."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import webbrowser
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from ...ai.schema import Intent, SkillResult
from ..base import Skill

SITES = {
    "youtube": "https://www.youtube.com/",
    "google": "https://www.google.com/",
    "gmail": "https://mail.google.com/",
    "github": "https://github.com/",
    "reddit": "https://www.reddit.com/",
    "wikipedia": "https://en.wikipedia.org/",
    "netflix": "https://www.netflix.com/",
    "spotify": "https://open.spotify.com/",
    "amazon": "https://www.amazon.co.uk/",
}
_BROWSER_TARGETS = {
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
}
_PRIVATE_FLAGS = {
    "chrome.exe": "--incognito",
    "msedge.exe": "--inprivate",
    "firefox.exe": "-private-window",
}
_BROWSER_ACTIONS = {
    "new_tab": (0x11, ord("T")),
    "close_tab": (0x11, ord("W")),
    "reopen_tab": (0x11, 0x10, ord("T")),
    "reload": (0x11, ord("R")),
    "back": (0x12, 0x25),
    "forward": (0x12, 0x27),
    "downloads": (0x11, ord("J")),
    "history": (0x11, ord("H")),
    "find": (0x11, ord("F")),
}
_BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}


def _url_for(site: str) -> str | None:
    value = site.strip()
    known = SITES.get(value.casefold())
    if known:
        return known
    if re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s]*)?", value):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _browser_executable(browser: str) -> str | None:
    target = _BROWSER_TARGETS.get(browser.casefold())
    if target is None:
        return None
    found = shutil.which(target)
    if found:
        return found
    roots = [os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"),
             os.getenv("LOCALAPPDATA")]
    suffixes = {
        "chrome.exe": ("Google/Chrome/Application/chrome.exe",),
        "msedge.exe": ("Microsoft/Edge/Application/msedge.exe",),
        "firefox.exe": ("Mozilla Firefox/firefox.exe",),
    }[target]
    for root in filter(None, roots):
        for suffix in suffixes:
            candidate = Path(root) / suffix
            if candidate.is_file():
                return str(candidate)
    return None


def _private_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "private", "incognito"}:
        return True
    if normalized in {"false", "0", "no", "normal"}:
        return False
    raise ValueError("private must be a boolean")


def _private_browser(preferred: str = "") -> tuple[str, str] | None:
    if preferred:
        executable = _browser_executable(preferred)
        return (preferred, executable) if executable else None
    for name in ("Google Chrome", "Microsoft Edge", "Firefox"):
        executable = _browser_executable(name)
        if executable:
            return name, executable
    return None


def _foreground_browser() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import psutil
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = psutil.Process(pid.value).name().lower()
        return name if name in _BROWSER_PROCESSES else None
    except Exception:
        return None


def _send_browser_action(action: str) -> bool:
    keys = _BROWSER_ACTIONS.get(action)
    if not keys or _foreground_browser() is None:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, 0x0002, 0)
    return True


class BrowserSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if intent.type == "browser_action":
            action = str(intent.get("action", "")).strip().lower()
            if action not in _BROWSER_ACTIONS:
                return self.fail("That browser action is not supported.")
            if not _send_browser_action(action):
                return self.fail("A supported browser must be the active window.",
                                 speak="Please focus the browser first, sir.")
            phrase = action.replace("_", " ").title()
            return self.ok(f"Browser action: {phrase}.",
                           speak=f"{phrase}, sir.", action=action)
        try:
            private = _private_value(intent.get("private", False))
        except ValueError as exc:
            return self.fail(str(exc))
        browser = str(intent.get("browser", "")).strip()
        if intent.type == "open_browser":
            url, label = "about:blank", "a browser window"
        elif intent.type == "search_browser":
            query = str(intent.get("query", "")).strip()
            if not query or len(query) > 500:
                return self.fail("A search query of 1-500 characters is required.")
            url = "https://www.google.com/search?q=" + quote_plus(query)
            label = f"search results for {query}"
        else:
            site = str(intent.get("site", "")).strip()
            url = _url_for(site)
            if url is None:
                return self.fail(
                    "Provide a known website name or a valid HTTP URL.",
                    speak="I couldn't identify that website, sir.")
            label = site if site.casefold() in SITES else urlparse(url).netloc
        try:
            if browser or private:
                selected = _private_browser(browser)
                if selected is None:
                    requested = browser or "a private-capable browser"
                    return self.fail(f"Browser '{requested}' is not installed.",
                                     speak=f"I couldn't find {requested}, sir.")
                selected_name, executable = selected
                arguments = [executable]
                if private:
                    arguments.append(_PRIVATE_FLAGS[Path(executable).name.lower()])
                arguments.append(url)
                subprocess.Popen(arguments, close_fds=True)
                browser = selected_name
            elif not webbrowser.open(url):
                return self.fail("The default browser rejected the request.")
        except OSError as exc:
            return self.fail(f"Could not open the website: {exc}",
                             speak="I couldn't open that website, sir.")
        mode = "private " if private else ""
        destination = f" in {mode}{browser}" if browser else ""
        return self.ok(f"Opening {label}{destination}.",
                       speak=f"Opening {label}{destination}, sir.",
                       site=label, url=url, browser=browser or "default",
                       private=private)


SKILL = BrowserSkill()
