"""§16.5 autonomous task system: scheduled, recurring and condition triggers.

A :class:`Task` fires when its trigger is satisfied; firing produces a reminder
the engine surfaces as a suggestion. **Firing never executes anything** — the
task system schedules *prompts to the user*, keeping the §1/§16.3 "never act
without permission" rule intact.

Trigger kinds:
  * ``scheduled``  — fire once at/after an absolute ISO time (``due``).
  * ``recurring``  — fire every ``interval`` seconds.
  * ``condition``  — fire when a simple snapshot expression is true, e.g.
    ``"cpu > 90"`` or ``"idle_seconds > 600"`` (re-arms once the condition clears).

Tasks persist to ``data/tasks.json`` so they survive restarts and can be
hand-edited.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_CONDITION = re.compile(
    r"^\s*(cpu|memory|idle_seconds|app_count)\s*(<|<=|>|>=|==)\s*([\d.]+)\s*$")
_FIELDS = {"cpu", "memory", "idle_seconds", "app_count"}


@dataclass
class Task:
    id: str
    kind: str                       # scheduled | recurring | condition
    description: str
    due: str = ""                   # ISO time (scheduled)
    interval: float = 0.0           # seconds (recurring)
    condition: str = ""             # expression (condition)
    enabled: bool = True
    last_fired: float = 0.0         # epoch seconds
    _armed: bool = True             # condition re-arm flag (not persisted as state)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "description": self.description,
                "due": self.due, "interval": self.interval,
                "condition": self.condition, "enabled": self.enabled,
                "last_fired": self.last_fired}


def _eval_condition(expr: str, snap) -> bool:
    m = _CONDITION.match(expr or "")
    if not m:
        return False
    field_name, op, num = m.group(1), m.group(2), float(m.group(3))
    value = float(getattr(snap, field_name, 0.0))
    return {
        "<": value < num, "<=": value <= num, ">": value > num,
        ">=": value >= num, "==": value == num,
    }[op]


class TaskScheduler:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        for raw in data.get("tasks", []):
            try:
                self._tasks[raw["id"]] = Task(
                    id=raw["id"], kind=raw["kind"],
                    description=raw.get("description", ""),
                    due=raw.get("due", ""), interval=float(raw.get("interval", 0)),
                    condition=raw.get("condition", ""),
                    enabled=bool(raw.get("enabled", True)),
                    last_fired=float(raw.get("last_fired", 0)))
            except Exception:
                continue

    def _save(self) -> None:
        payload = {"tasks": [t.as_dict() for t in self._tasks.values()]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- management ----------------------------------------------------------
    def add(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.id] = task
            self._save()
            return task

    def remove(self, task_id: str) -> bool:
        with self._lock:
            ok = self._tasks.pop(task_id, None) is not None
            if ok:
                self._save()
            return ok

    def list(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    # -- evaluation ----------------------------------------------------------
    def due_tasks(self, snap, now: float | None = None) -> list[Task]:
        """Return tasks whose trigger fired this tick (and update their state)."""
        now = now if now is not None else time.time()
        fired: list[Task] = []
        with self._lock:
            for task in self._tasks.values():
                if not task.enabled:
                    continue
                if self._fires(task, snap, now):
                    task.last_fired = now
                    fired.append(task)
            if fired:
                self._save()
        return fired

    def _fires(self, task: Task, snap, now: float) -> bool:
        if task.kind == "scheduled":
            if task.last_fired or not task.due:
                return False
            try:
                return datetime.now() >= datetime.fromisoformat(task.due)
            except ValueError:
                return False
        if task.kind == "recurring":
            return task.interval > 0 and (now - task.last_fired) >= task.interval
        if task.kind == "condition":
            active = _eval_condition(task.condition, snap)
            if active and task._armed:
                task._armed = False        # fire once until it clears
                return True
            if not active:
                task._armed = True         # re-arm when condition clears
            return False
        return False
