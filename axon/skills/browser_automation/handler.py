"""Thread-affine Playwright browser with bounded, observable DOM actions."""
from __future__ import annotations

import ipaddress
import importlib.util
import hashlib
import queue
import re
import socket
import threading
from functools import lru_cache
from urllib.parse import urlparse

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_MAX_TEXT = 1000
_ELEMENT_ID = re.compile(r"^e[1-9][0-9]{0,3}$")


def _fingerprint(page) -> dict:
    """Return a bounded page-state fingerprint without retaining page content."""
    state = page.evaluate("""() => ({
      title: document.title || '',
      text: (document.body?.innerText || '').slice(0, 12000),
      controls: Array.from(document.querySelectorAll(
        'a,button,input,textarea,select,[role="button"],[role="link"]'))
        .slice(0, 200).map(e => [e.tagName, e.innerText || e.value || '',
          e.disabled || false, e.checked || false, e.getAttribute('aria-expanded')])
    })""")
    digest = hashlib.sha256(repr(state).encode("utf-8", "replace")).hexdigest()[:16]
    return {"url": page.url, "title": str(state.get("title", "")),
            "digest": digest}


def _ground_elements(page) -> list[dict]:
    """Label visible interactive DOM nodes with stable, session-local IDs."""
    return page.evaluate("""() => {
      const selector = 'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]';
      let next = Number(document.documentElement.dataset.axonNextId || '1');
      const output = [];
      for (const el of Array.from(document.querySelectorAll(selector))) {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        if (rect.width < 2 || rect.height < 2 || style.visibility === 'hidden' || style.display === 'none') continue;
        if (!el.dataset.axonId) el.dataset.axonId = `e${next++}`;
        const label = (el.getAttribute('aria-label') || el.innerText || el.value ||
          el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 160);
        output.push({id: el.dataset.axonId, role: el.getAttribute('role') ||
          ({A:'link',BUTTON:'button',INPUT:'input',TEXTAREA:'textbox',SELECT:'select'}[el.tagName] || el.tagName.toLowerCase()),
          label, disabled: Boolean(el.disabled), x: Math.round(rect.x), y: Math.round(rect.y),
          width: Math.round(rect.width), height: Math.round(rect.height)});
        if (output.length >= 100) break;
      }
      document.documentElement.dataset.axonNextId = String(next);
      return output;
    }""")


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
            response = page.goto(params["url"], wait_until="domcontentloaded")
            after = _fingerprint(page)
            verified = bool(after["url"] and after["title"] or after["digest"])
            return {"ok": verified, "title": page.title(), "url": page.url,
                    "status": response.status if response else None,
                    "verification": {"verified": verified,
                                     "reason": "page loaded and state captured",
                                     "after": after}}
        if action == "read":
            text = page.locator("body").inner_text()[:8000]
            links = page.locator("a").evaluate_all(
                "els => els.slice(0,30).map(a => ({text:(a.innerText||'').trim(), url:a.href})).filter(x => x.text)")
            elements = _ground_elements(page)
            return {"ok": True, "title": page.title(), "url": page.url,
                    "text": text, "links": links, "elements": elements,
                    "state": _fingerprint(page)}
        if action == "click":
            target, element_id = params.get("target", ""), params.get("element_id", "")
            _ground_elements(page)
            locator = page.locator(f'[data-axon-id="{element_id}"]') if element_id else \
                page.get_by_role("link", name=target, exact=False)
            if not element_id and locator.count() == 0:
                locator = page.get_by_role("button", name=target, exact=False)
            if not element_id and locator.count() == 0:
                locator = page.get_by_text(target, exact=False)
            if locator.count() == 0:
                return {"ok": False, "error": "No matching visible element was found."}
            before = _fingerprint(page)
            locator.first.click()
            page.wait_for_timeout(650)
            after = _fingerprint(page)
            changed = before != after
            expected = str(params.get("expected", "")).strip()
            expected_met = True
            if expected:
                expected_met = page.get_by_text(expected, exact=False).count() > 0
            verified = changed and expected_met
            reason = ("page state changed" if changed else "no observable page change")
            if expected and not expected_met:
                reason = f"expected outcome not found: {expected}"
            return {"ok": verified, "title": page.title(), "url": page.url,
                    "clicked": element_id or target,
                    "verification": {"verified": verified, "reason": reason,
                                     "expected": expected or None,
                                     "before": before, "after": after}}
        if action == "fill":
            field, text = params.get("field", ""), params["text"]
            element_id = params.get("element_id", "")
            _ground_elements(page)
            locator = page.locator(f'[data-axon-id="{element_id}"]') if element_id else \
                page.get_by_label(field, exact=False)
            if not element_id and locator.count() == 0:
                locator = page.get_by_placeholder(field, exact=False)
            if locator.count() == 0:
                return {"ok": False, "error": "No matching form field was found."}
            locator.first.fill(text)
            actual = locator.first.evaluate(
                "e => ('value' in e ? e.value : (e.textContent || ''))")
            verified = actual == text
            return {"ok": verified, "title": page.title(), "url": page.url,
                    "field": element_id or field, "characters": len(text),
                    "verification": {"verified": verified,
                                     "reason": "field value matches requested text" if verified
                                               else "field value did not match"}}
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
            element_id = str(intent.get("element_id", "")).strip().lower()
            if not element_id and not 1 <= len(target) <= 200:
                return self.fail("Provide a click target or grounded element ID.")
            if element_id and not _ELEMENT_ID.fullmatch(element_id):
                return self.fail("Grounded element IDs use the form e1, e2, and so on.")
            expected = str(intent.get("expected", "")).strip()
            if len(expected) > 200:
                return self.fail("Expected outcomes must be at most 200 characters.")
            params.update(target=target, element_id=element_id, expected=expected)
        elif action == "fill":
            field = str(intent.get("field", "")).strip()
            text = str(intent.get("text", ""))
            element_id = str(intent.get("element_id", "")).strip().lower()
            if (not element_id and not 1 <= len(field) <= 160) or not 1 <= len(text) <= _MAX_TEXT:
                return self.fail("A field or grounded element ID and 1-1000 characters of text are required.")
            if element_id and not _ELEMENT_ID.fullmatch(element_id):
                return self.fail("Grounded element IDs use the form e1, e2, and so on.")
            params.update(field=field, text=text, element_id=element_id)
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
        verification = result.get("verification") or {}
        suffix = " [verified]" if verification.get("verified") else ""
        summary = (f"{action}: {result.get('title') or result.get('url') or 'done'}{suffix}")
        return self.ok(summary, speak=speak, **{
            key: value for key, value in result.items() if key != "ok"})


SKILL = BrowserAutomationSkill()
