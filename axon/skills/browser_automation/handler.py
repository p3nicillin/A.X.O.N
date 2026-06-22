"""Thread-affine Playwright browser with bounded, observable DOM actions."""
from __future__ import annotations

import ipaddress
import importlib.util
import queue
import socket
import threading
from functools import lru_cache
from urllib.parse import urlparse

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_MAX_TEXT = 1000


@lru_cache(maxsize=256)
def _public_url(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443,
                                       type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(info[4][0]).is_global:
                return None
    except (OSError, ValueError):
        return None
    return raw


class PlaywrightWorker:
    def __init__(self, *, headless: bool = False, timeout: float = 20.0) -> None:
        self.headless = headless
        self.timeout = max(5.0, min(float(timeout), 60.0))
        self._queue: queue.Queue = queue.Queue(maxsize=16)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def perform(self, action: str, parameters: dict) -> dict:
        self._ensure_thread()
        done = threading.Event()
        box: dict = {}
        try:
            self._queue.put((action, parameters, done, box), timeout=1.0)
        except queue.Full:
            return {"ok": False, "error": "managed browser queue is busy"}
        if not done.wait(self.timeout + 10.0):
            return {"ok": False, "error": "managed browser action timed out"}
        return box.get("result", {"ok": False, "error": "no browser result"})

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop,
                                            name="axon-playwright", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        playwright = browser = context = page = None
        init_error = ""
        try:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
        except Exception as exc:
            init_error = ("Playwright is unavailable. Install the Python package "
                          f"and Chromium: {exc}")
        while True:
            item = self._queue.get()
            if item is None:
                break
            action, params, done, box = item
            try:
                if init_error:
                    result = {"ok": False, "error": init_error}
                else:
                    if action == "close":
                        if context is not None:
                            context.close()
                        if browser is not None:
                            browser.close()
                        browser = context = page = None
                        result = {"ok": True, "closed": True}
                    else:
                        if browser is None:
                            browser = playwright.chromium.launch(headless=self.headless)
                            context = browser.new_context(
                                accept_downloads=False, locale="en-GB")
                            context.route("**/*", self._guard_request)
                            page = context.new_page()
                            page.set_default_timeout(self.timeout * 1000)
                        result = self._act(page, action, params)
                box["result"] = result
            except Exception as exc:
                box["result"] = {"ok": False,
                                 "error": f"managed browser failed: {exc}"}
            finally:
                done.set()
        try:
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()
            if playwright is not None:
                playwright.stop()
        except Exception:
            pass

    @staticmethod
    def _guard_request(route, request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"about", "data", "blob"}:
            route.continue_()
        elif parsed.scheme in {"http", "https"} and _public_url(request.url):
            route.continue_()
        else:
            route.abort()

    @staticmethod
    def _act(page, action: str, params: dict) -> dict:
        if action == "navigate":
            page.goto(params["url"], wait_until="domcontentloaded")
            return {"ok": True, "title": page.title(), "url": page.url}
        if action == "read":
            text = page.locator("body").inner_text()[:8000]
            links = page.locator("a").evaluate_all(
                "els => els.slice(0,30).map(a => ({text:(a.innerText||'').trim(), url:a.href})).filter(x => x.text)")
            return {"ok": True, "title": page.title(), "url": page.url,
                    "text": text, "links": links}
        if action == "click":
            target = params["target"]
            locator = page.get_by_role("link", name=target, exact=False)
            if locator.count() == 0:
                locator = page.get_by_role("button", name=target, exact=False)
            if locator.count() == 0:
                locator = page.get_by_text(target, exact=False)
            locator.first.click()
            page.wait_for_timeout(400)
            return {"ok": True, "title": page.title(), "url": page.url,
                    "clicked": target}
        if action == "fill":
            field, text = params["field"], params["text"]
            locator = page.get_by_label(field, exact=False)
            if locator.count() == 0:
                locator = page.get_by_placeholder(field, exact=False)
            locator.first.fill(text)
            return {"ok": True, "title": page.title(), "url": page.url,
                    "field": field, "characters": len(text)}
        return {"ok": False, "error": "unsupported managed browser action"}

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread.join(timeout=5.0)
        self._thread = None


class BrowserAutomationSkill(Skill):
    def __init__(self) -> None:
        self.worker = PlaywrightWorker()

    def configure(self, config) -> None:
        self.worker.stop()
        self.worker = PlaywrightWorker(
            headless=bool(getattr(config, "browser_automation_headless", False)),
            timeout=float(getattr(config, "browser_automation_timeout", 20.0)))

    def stop(self) -> None:
        self.worker.stop()

    def status(self) -> dict:
        return {"playwright_installed": importlib.util.find_spec("playwright") is not None,
                "active": bool(self.worker._thread and self.worker._thread.is_alive()),
                "isolated": True}

    def execute(self, intent: Intent) -> SkillResult:
        action = {
            "browser_navigate": "navigate", "browser_read_page": "read",
            "browser_click": "click", "browser_fill": "fill",
            "browser_close_managed": "close",
        }.get(intent.type)
        if action is None:
            return self.fail(f"Unsupported browser automation '{intent.type}'.")
        params: dict = {}
        if action == "navigate":
            url = _public_url(str(intent.get("url", "")))
            if url is None:
                return self.fail("Provide a public HTTP or HTTPS URL.")
            params["url"] = url
        elif action == "click":
            target = str(intent.get("target", "")).strip()
            if not 1 <= len(target) <= 200:
                return self.fail("Click targets must contain 1-200 characters.")
            params["target"] = target
        elif action == "fill":
            field = str(intent.get("field", "")).strip()
            text = str(intent.get("text", ""))
            if not 1 <= len(field) <= 160 or not 1 <= len(text) <= _MAX_TEXT:
                return self.fail("A field and 1-1000 characters of text are required.")
            params.update(field=field, text=text)
        result = self.worker.perform(action, params)
        if not result.get("ok"):
            return self.fail(str(result.get("error", "Browser automation failed.")),
                             speak="The managed browser action failed, sir.")
        if action == "read":
            preview = " ".join(str(result.get("text", "")).split())[:700]
            speak = f"The page says: {preview}, sir." if preview else \
                "The page contains no readable text, sir."
        elif action == "navigate":
            speak = f"Opened {result.get('title') or result.get('url')}, sir."
        else:
            speak = f"Managed browser {action} complete, sir."
        summary = (f"{action}: {result.get('title') or result.get('url') or 'done'}")
        return self.ok(summary, speak=speak, **{
            key: value for key, value in result.items() if key != "ok"})


SKILL = BrowserAutomationSkill()
