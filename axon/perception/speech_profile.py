"""Persistent transcript adaptation without retaining biometric audio."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from ..config import DATA_DIR

PROFILE_PATH = DATA_DIR / "speech_profile.json"


class SpeechProfile:
    """Learns recurring recognizer mistakes as phrase-level corrections."""

    def __init__(self, path: Path = PROFILE_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._corrections: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            corrections = raw.get("corrections", {}) if isinstance(raw, dict) else {}
            if isinstance(corrections, dict):
                self._corrections = {
                    str(k): str(v) for k, v in corrections.items()
                    if str(k).strip() and str(v).strip()
                }
        except (OSError, ValueError, TypeError):
            self._corrections = {}

    @staticmethod
    def _validate(heard: str, expected: str) -> tuple[str, str]:
        heard, expected = heard.strip(), expected.strip()
        if not 1 <= len(heard) <= 80:
            raise ValueError("heard phrase must contain 1-80 characters")
        if not 1 <= len(expected) <= 160:
            raise ValueError("intended phrase must contain 1-160 characters")
        return heard, expected

    def add(self, heard: str, expected: str) -> dict:
        heard, expected = self._validate(heard, expected)
        with self._lock:
            if len(self._corrections) >= 100 and heard.casefold() not in {
                    key.casefold() for key in self._corrections}:
                raise ValueError("speech profile is limited to 100 corrections")
            # Replace a case-insensitive duplicate rather than creating two.
            duplicate = next((key for key in self._corrections
                              if key.casefold() == heard.casefold()), None)
            if duplicate is not None:
                del self._corrections[duplicate]
            self._corrections[heard] = expected
            self._save()
        return {"heard": heard, "expected": expected}

    def remove(self, heard: str) -> bool:
        needle = str(heard).strip().casefold()
        with self._lock:
            key = next((key for key in self._corrections
                        if key.casefold() == needle), None)
            if key is None:
                return False
            del self._corrections[key]
            self._save()
            return True

    def apply(self, transcript: str) -> str:
        text = str(transcript or "").strip()
        with self._lock:
            corrections = sorted(self._corrections.items(),
                                 key=lambda item: len(item[0]), reverse=True)
        for heard, expected in corrections:
            pattern = r"(?<!\w)" + re.escape(heard) + r"(?!\w)"
            text = re.sub(pattern, expected, text, flags=re.IGNORECASE)
        return text

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"heard": heard, "expected": expected}
                    for heard, expected in sorted(
                        self._corrections.items(), key=lambda item: item[0].lower())]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"version": 1,
                                         "corrections": self._corrections},
                                        indent=2, sort_keys=True),
                             encoding="utf-8")
        temporary.replace(self.path)
