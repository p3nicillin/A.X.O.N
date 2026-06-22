"""Persistent local timers and reminders with lifecycle-managed alerts."""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ...ai.schema import Intent, SkillResult
from ...config import DATA_DIR
from ...core.event_bus import Event
from ..base import Skill

REMINDERS_FILE = DATA_DIR / "reminders.json"
_MAX_DELAY = 31 * 24 * 60 * 60


class ReminderSkill(Skill):
    def __init__(self, path: Path = REMINDERS_FILE) -> None:
        self.path = path
        self._items: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bus = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            now = time.time()
            for item in raw.get("reminders", []):
                if (isinstance(item, dict) and item.get("id")
                        and float(item.get("due", 0)) > now - 86400):
                    self._items[str(item["id"])] = {
                        "id": str(item["id"]),
                        "label": str(item.get("label", "Reminder"))[:200],
                        "due": float(item["due"]),
                        "kind": str(item.get("kind", "reminder")),
                    }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._items = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"reminders": list(self._items.values())},
                                   indent=2), encoding="utf-8")
        temp.replace(self.path)

    def start(self, bus=None) -> None:
        with self._lock:
            self._bus = bus
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="axon-reminders", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None
        self._bus = None

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            self._fire_due()

    def _fire_due(self, now: float | None = None) -> list[dict]:
        current = time.time() if now is None else now
        with self._lock:
            due = [item.copy() for item in self._items.values()
                   if item["due"] <= current]
            for item in due:
                self._items.pop(item["id"], None)
            if due:
                self._save()
            bus = self._bus
        if bus is not None:
            for item in due:
                if self._thread is not None and self._thread.is_alive():
                    self._notify(item)
                bus.publish(Event.REMINDER_DUE, item)
        return due

    @staticmethod
    def _notify(item: dict) -> None:
        """Best-effort native toast; the in-app event remains authoritative."""
        try:
            from winotify import Notification
            Notification(app_id="AXON", title="AXON reminder",
                         msg=str(item.get("label", "Reminder")),
                         duration="short").show()
        except Exception:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass

    def snapshot(self) -> list[dict]:
        with self._lock:
            return sorted((item.copy() for item in self._items.values()),
                          key=lambda item: item["due"])

    def execute(self, intent: Intent) -> SkillResult:
        if intent.type in {"set_timer", "set_reminder"}:
            return self._set(intent)
        if intent.type == "list_reminders":
            return self._list()
        if intent.type == "cancel_reminder":
            return self._cancel(intent)
        return self.fail(f"Unsupported reminder action '{intent.type}'.")

    def _set(self, intent: Intent) -> SkillResult:
        try:
            seconds = float(intent.get("seconds", 0))
        except (TypeError, ValueError):
            seconds = 0
        if not math.isfinite(seconds) or seconds < 1 or seconds > _MAX_DELAY:
            return self.fail("The delay must be between 1 second and 31 days.")
        kind = "timer" if intent.type == "set_timer" else "reminder"
        default = "Timer complete" if kind == "timer" else "Reminder"
        label = str(intent.get("label", default)).strip() or default
        if len(label) > 200:
            return self.fail("Reminder labels are limited to 200 characters.")
        item = {"id": uuid.uuid4().hex[:8], "label": label,
                "due": time.time() + seconds, "kind": kind}
        with self._lock:
            self._items[item["id"]] = item
            self._save()
        due_text = datetime.fromtimestamp(item["due"]).strftime("%H:%M:%S")
        spoken = (f"{kind.title()} set for {self._duration_text(seconds)}, sir."
                  if label == default else
                  f"I'll remind you to {label} in {self._duration_text(seconds)}, sir.")
        return self.ok(f"{kind.title()} {item['id']} due at {due_text}: {label}",
                       speak=spoken, **item)

    def _list(self) -> SkillResult:
        with self._lock:
            items = sorted((item.copy() for item in self._items.values()),
                           key=lambda item: item["due"])
        if not items:
            return self.ok("No active timers or reminders.",
                           speak="You have no active timers or reminders, sir.",
                           reminders=[], count=0)
        now = time.time()
        entries = [f"{item['id']} {item['label']} in "
                   f"{self._duration_text(max(0, item['due'] - now))}"
                   for item in items]
        spoken_entries = [f"{item['label']} in "
                          f"{self._duration_text(max(0, item['due'] - now))}"
                          for item in items[:5]]
        return self.ok(" | ".join(entries),
                       speak="Active reminders: " + "; ".join(spoken_entries)
                       + ", sir.", reminders=items, count=len(items))

    def _cancel(self, intent: Intent) -> SkillResult:
        identifier = str(intent.get("identifier", "")).strip().casefold()
        with self._lock:
            if not identifier and len(self._items) == 1:
                matches = list(self._items.values())
            elif identifier:
                matches = [item for item in self._items.values()
                           if item["id"].casefold() == identifier
                           or identifier in item["label"].casefold()]
            else:
                matches = []
            if not matches:
                return self.fail(
                    "No matching reminder was found. Use list reminders for IDs.",
                    speak="I couldn't identify which reminder to cancel, sir.")
            if len(matches) > 1:
                ids = ", ".join(item["id"] for item in matches[:5])
                return self.fail(f"Multiple reminders matched: {ids}.",
                                 speak="More than one reminder matched, sir.")
            item = self._items.pop(matches[0]["id"])
            self._save()
        return self.ok(f"Cancelled {item['id']}: {item['label']}",
                       speak=f"Cancelled {item['label']}, sir.", **item)

    @staticmethod
    def _duration_text(seconds: float) -> str:
        rounded = max(0, int(round(seconds)))
        if rounded >= 86400 and rounded % 86400 == 0:
            count, unit = rounded // 86400, "day"
        elif rounded >= 3600 and rounded % 3600 == 0:
            count, unit = rounded // 3600, "hour"
        elif rounded >= 60 and rounded % 60 == 0:
            count, unit = rounded // 60, "minute"
        else:
            count, unit = rounded, "second"
        return f"{count} {unit}{'' if count == 1 else 's'}"


SKILL = ReminderSkill()
