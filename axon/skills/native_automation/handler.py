"""Bounded, active-window-only Win32 control grounding and verification."""
from __future__ import annotations

import ctypes
import hashlib
import queue
import re
import sys
import threading
import time
from ctypes import wintypes

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_ELEMENT_ID = re.compile(r"^n[1-9][0-9]{0,3}$")
_MAX_CONTROLS = 120
_MAX_TEXT = 1000
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_WM_SETTEXT = 0x000C
_BM_CLICK = 0x00F5
_SMTO_ABORTIFHUNG = 0x0002
_GWL_STYLE = -16
_ES_PASSWORD = 0x0020


def _user32():
    return ctypes.windll.user32 if sys.platform == "win32" else None


def _send(hwnd: int, message: int, wparam=0, lparam=0,
          timeout_ms: int = 1200) -> tuple[bool, int]:
    """Use SendMessageTimeout so a hung target cannot stall AXON."""
    user32 = _user32()
    if user32 is None:
        return False, 0
    result = ctypes.c_size_t()
    try:
        ok = user32.SendMessageTimeoutW(
            wintypes.HWND(hwnd), wintypes.UINT(message),
            wintypes.WPARAM(wparam), lparam, _SMTO_ABORTIFHUNG,
            wintypes.UINT(timeout_ms), ctypes.byref(result))
        return bool(ok), int(result.value)
    except Exception:
        return False, 0


def _text(hwnd: int) -> str:
    ok, length = _send(hwnd, _WM_GETTEXTLENGTH)
    if ok and 0 <= length <= 32768:
        buffer = ctypes.create_unicode_buffer(length + 1)
        got, _ = _send(hwnd, _WM_GETTEXT, length + 1,
                       ctypes.cast(buffer, ctypes.c_void_p))
        if got:
            return buffer.value
    user32 = _user32()
    if user32 is None:
        return ""
    length = min(32768, max(0, int(user32.GetWindowTextLengthW(hwnd))))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class_name(hwnd: int) -> str:
    user32 = _user32()
    if user32 is None:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _is_protected(hwnd: int) -> bool:
    user32 = _user32()
    if user32 is None or "edit" not in _class_name(hwnd).casefold():
        return False
    getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    return bool(int(getter(hwnd, _GWL_STYLE)) & _ES_PASSWORD)


def _control_text(hwnd: int) -> str:
    return "" if _is_protected(hwnd) else _text(hwnd)


def _children(root: int) -> list[int]:
    user32 = _user32()
    if user32 is None:
        return []
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)

    @callback_type
    def collect(hwnd, _lparam):
        if len(handles) >= _MAX_CONTROLS:
            return False
        if user32.IsWindowVisible(hwnd):
            handles.append(int(hwnd))
        return True

    user32.EnumChildWindows(wintypes.HWND(root), collect, 0)
    return handles


def _fingerprint(root: int) -> str:
    user32 = _user32()
    state = [(_class_name(hwnd), _control_text(hwnd)[:160],
              bool(user32.IsWindowEnabled(hwnd))) for hwnd in _children(root)]
    raw = (_text(root)[:200], state)
    return hashlib.sha256(repr(raw).encode("utf-8", "replace")).hexdigest()[:16]


def _describe(element_id: str, hwnd: int) -> dict:
    user32 = _user32()
    rect = wintypes.RECT()
    bounds = None
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        bounds = {"x": int(rect.left), "y": int(rect.top),
                  "width": int(rect.right - rect.left),
                  "height": int(rect.bottom - rect.top)}
    return {"id": element_id, "role": _class_name(hwnd)[:80],
            "label": "<protected>" if _is_protected(hwnd)
            else _control_text(hwnd)[:160],
            "enabled": bool(user32.IsWindowEnabled(hwnd)), "bounds": bounds}


class NativeUIWorker:
    """Serialise native actions and retain only the latest handle snapshot."""

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = max(5.0, min(float(timeout), 45.0))
        self._queue: queue.Queue = queue.Queue(maxsize=12)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def perform(self, action: str, parameters: dict) -> dict:
        self._ensure_thread()
        done, box = threading.Event(), {}
        try:
            self._queue.put((action, parameters, done, box), timeout=1.0)
        except queue.Full:
            return {"ok": False, "error": "native automation queue is busy"}
        if not done.wait(self.timeout + 3.0):
            return {"ok": False, "error": "native automation timed out"}
        return box.get("result", {"ok": False, "error": "no native automation result"})

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop,
                                            name="axon-native-win32", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        root = 0
        elements: dict[str, int] = {}
        while True:
            item = self._queue.get()
            if item is None:
                break
            action, params, done, box = item
            try:
                if sys.platform != "win32":
                    result = {"ok": False, "error": "Native automation requires Windows."}
                elif action == "inspect":
                    root = int(_user32().GetForegroundWindow())
                    if not root:
                        result = {"ok": False, "error": "No active window was found."}
                    else:
                        handles = _children(root)
                        elements = {f"n{i}": hwnd
                                    for i, hwnd in enumerate(handles, 1)}
                        records = [_describe(key, hwnd)
                                   for key, hwnd in elements.items()]
                        result = {"ok": True, "window": _text(root)[:200],
                                  "handle": root, "elements": records,
                                  "count": len(records), "state": _fingerprint(root)}
                else:
                    result = self._act(root, elements, action, params)
                box["result"] = result
            except Exception as exc:
                box["result"] = {"ok": False,
                                 "error": f"native automation failed: {exc}"}
            finally:
                done.set()

    @staticmethod
    def _act(root: int, elements: dict[str, int], action: str,
             params: dict) -> dict:
        user32 = _user32()
        if not root or not elements:
            return {"ok": False, "error": "Inspect the active application first."}
        if int(user32.GetForegroundWindow()) != root:
            return {"ok": False, "error": "The active application changed; inspect it again."}
        element_id, hwnd = params["element_id"], elements.get(params["element_id"])
        if not hwnd or not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return {"ok": False, "error": "That grounded control is no longer available."}
        if not user32.IsWindowEnabled(hwnd):
            return {"ok": False, "error": "That control is disabled."}
        before = _fingerprint(root)
        if action == "click":
            sent, _ = _send(hwnd, _BM_CLICK)
            if not sent:
                return {"ok": False, "error": "The target application rejected the click."}
            time.sleep(0.35)
            current = int(user32.GetForegroundWindow()) or root
            after = _fingerprint(current)
            expected = str(params.get("expected", "")).casefold()
            expected_met = not expected or any(
                expected in _control_text(item).casefold()
                for item in [current, *_children(current)])
            changed = before != after or current != root
            verified = changed and expected_met
            reason = "application state changed" if changed else "no observable application change"
            if expected and not expected_met:
                reason = "expected outcome was not found"
            return {"ok": verified, "element_id": element_id,
                    "verification": {"verified": verified, "reason": reason,
                                     "expected_met": expected_met,
                                     "before": before, "after": after}}
        if action == "fill":
            if _is_protected(hwnd):
                return {"ok": False,
                        "error": "Protected credential fields are not automated."}
            text = params["text"]
            value = ctypes.c_wchar_p(text)
            sent, _ = _send(hwnd, _WM_SETTEXT, 0,
                            ctypes.cast(value, ctypes.c_void_p))
            verified = sent and _control_text(hwnd) == text
            return {"ok": verified, "element_id": element_id,
                    "characters": len(text),
                    "verification": {"verified": verified,
                                     "reason": "control value matches requested text"
                                     if verified else "control value did not match"}}
        return {"ok": False, "error": "unsupported native automation action"}

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


class NativeAutomationSkill(Skill):
    def __init__(self) -> None:
        self.worker = NativeUIWorker()

    def configure(self, config) -> None:
        self.worker.stop()
        self.worker = NativeUIWorker(
            timeout=float(getattr(config, "native_automation_timeout", 15.0)))

    def stop(self) -> None:
        self.worker.stop()

    def status(self) -> dict:
        return {"available": sys.platform == "win32", "backend": "win32",
                "active": bool(self.worker._thread and self.worker._thread.is_alive()),
                "active_window_only": True, "verified_actions": True}

    def execute(self, intent: Intent) -> SkillResult:
        action = {"desktop_inspect": "inspect", "desktop_click": "click",
                  "desktop_fill": "fill"}.get(intent.type)
        if action is None:
            return self.fail(f"Unsupported native automation '{intent.type}'.")
        params = {}
        if action in {"click", "fill"}:
            element_id = str(intent.get("element_id", "")).strip().lower()
            if not _ELEMENT_ID.fullmatch(element_id):
                return self.fail("Use a grounded desktop control ID such as n1.")
            params["element_id"] = element_id
        if action == "click":
            expected = str(intent.get("expected", "")).strip()
            if len(expected) > 200:
                return self.fail("Expected outcomes must be at most 200 characters.")
            params["expected"] = expected
        elif action == "fill":
            text = str(intent.get("text", ""))
            if not 1 <= len(text) <= _MAX_TEXT:
                return self.fail("Desktop field text must contain 1-1000 characters.")
            params["text"] = text
        result = self.worker.perform(action, params)
        if not result.get("ok"):
            return self.fail(str(result.get("error") or
                                 (result.get("verification") or {}).get(
                                     "reason", "Native automation failed.")),
                             speak="The desktop action could not be verified, sir.")
        if action == "inspect":
            speak = f"I found {result.get('count', 0)} controls in {result.get('window') or 'the active application'}, sir."
        else:
            speak = f"Desktop {action} completed and verified, sir."
        return self.ok(f"native {action} complete", speak=speak, **{
            key: value for key, value in result.items() if key != "ok"})


SKILL = NativeAutomationSkill()
