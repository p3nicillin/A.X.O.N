"""Bounded Windows keyboard input using fixed Win32 APIs only."""
from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_MAX_TEXT = 1000
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD = 1

_NAMED_KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "escape": 0x1B, "esc": 0x1B, "space": 0x20, "pageup": 0x21,
    "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25,
    "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D,
    "delete": 0x2E, "win": 0x5B, "windows": 0x5B,
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT_VALUE(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _INPUT_VALUE)]


def _vk(name: str) -> int | None:
    name = name.strip().lower()
    if name in _NAMED_KEYS:
        return _NAMED_KEYS[name]
    if re.fullmatch(r"f(?:[1-9]|1[0-2])", name):
        return 0x70 + int(name[1:]) - 1
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    return None


class KeyboardSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        expected = {"type_text": {"text"}, "send_keystroke": {"keys"}}
        if intent.type not in expected:
            return self.fail(f"Unsupported keyboard action '{intent.type}'.")
        unknown = set(intent.parameters) - expected[intent.type]
        if unknown:
            return self.fail("Unsupported keyboard parameter(s): "
                             + ", ".join(sorted(unknown)))
        if os.name != "nt":
            return self.fail("Keyboard control is available only on Windows.")
        if intent.type == "type_text":
            return self._type(str(intent.get("text", "")))
        return self._shortcut(str(intent.get("keys", "")))

    def _type(self, text: str) -> SkillResult:
        if not text:
            return self.fail("No text was provided to type.")
        if len(text) > _MAX_TEXT:
            return self.fail(f"Text exceeds the {_MAX_TEXT}-character limit.")
        inputs: list[_INPUT] = []
        for char in text:
            code = ord(char)
            if code > 0xFFFF:
                return self.fail("Text contains an unsupported character.")
            inputs.extend((
                _INPUT(_INPUT_KEYBOARD, _INPUT_VALUE(
                    ki=_KEYBDINPUT(0, code, _KEYEVENTF_UNICODE, 0, 0))),
                _INPUT(_INPUT_KEYBOARD, _INPUT_VALUE(
                    ki=_KEYBDINPUT(0, code,
                                   _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
                                   0, 0))),
            ))
        array = (_INPUT * len(inputs))(*inputs)
        send_input = ctypes.windll.user32.SendInput
        send_input.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
        send_input.restype = wintypes.UINT
        sent = send_input(len(array), array, ctypes.sizeof(_INPUT))
        if sent != len(array):
            return self.fail("Windows rejected part of the keyboard input.")
        return self.ok(f"Typed {len(text)} characters.",
                       speak="Text entered, sir.", length=len(text))

    def _shortcut(self, keys: str) -> SkillResult:
        parts = [part.strip().lower() for part in re.split(r"\s*\+\s*", keys)
                 if part.strip()]
        codes = [_vk(part) for part in parts]
        if not parts or len(parts) > 4 or any(code is None for code in codes):
            return self.fail("Use one to four allow-listed keys joined with '+'.")
        if len(set(codes)) != len(codes):
            return self.fail("A shortcut cannot contain duplicate keys.")
        if {0x11, 0x12, 0x2E}.issubset(codes):
            return self.fail("Ctrl+Alt+Delete is not permitted.")
        user32 = ctypes.windll.user32
        for code in codes:
            user32.keybd_event(code, 0, 0, 0)
        for code in reversed(codes):
            user32.keybd_event(code, 0, _KEYEVENTF_KEYUP, 0)
        return self.ok(f"Sent shortcut {'+'.join(parts)}.",
                       speak="Keystroke sent, sir.")


SKILL = KeyboardSkill()
