"""Open validated websites without treating phrases as executable names."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

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


class BrowserSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        site = str(intent.get("site", "")).strip()
        browser = str(intent.get("browser", "")).strip()
        url = _url_for(site)
        if url is None:
            return self.fail("Provide a known website name or a valid HTTP URL.",
                             speak="I couldn't identify that website, sir.")
        try:
            if browser:
                executable = _browser_executable(browser)
                if executable is None:
                    return self.fail(f"Browser '{browser}' is not installed.",
                                     speak=f"I couldn't find {browser}, sir.")
                subprocess.Popen([executable, url], close_fds=True)
            elif not webbrowser.open(url):
                return self.fail("The default browser rejected the request.")
        except OSError as exc:
            return self.fail(f"Could not open the website: {exc}",
                             speak="I couldn't open that website, sir.")
        label = site if site.casefold() in SITES else urlparse(url).netloc
        destination = f" in {browser}" if browser else ""
        return self.ok(f"Opening {label}{destination}.",
                       speak=f"Opening {label}{destination}, sir.",
                       site=label, url=url, browser=browser or "default")


SKILL = BrowserSkill()
