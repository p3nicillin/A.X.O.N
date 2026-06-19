"""§17 user model: an inferred, persistent picture of the user.

Maintains the §17 profile shape::

    { preferences, habits, apps_used_most, verbosity_level, active_time_pattern }

It is fed two ways:
  * :meth:`observe` — called per executed command, accumulating app usage, intent
    frequencies and an hour-of-day histogram (→ habits / apps / time pattern).
  * :meth:`refresh_preferences` — pulls durable "preference" facts from the §4
    episodic vault (→ preferences / verbosity).

Raw counters are persisted alongside the derived profile so the model survives
restarts. Like every other reasoning layer it is observational only — it informs
responses, it never acts. The AI core consumes :meth:`hint_for_ai`.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

VERBOSITY_LEVELS = ("low", "medium", "high")

# intent type -> human habit phrase (for the habits summary)
_HABIT_PHRASES = {
    "get_time": "checks the time", "get_date": "checks the date",
    "system_info": "checks system status", "open_app": "opens applications",
    "close_app": "closes applications", "web_search": "runs web searches",
    "add_note": "takes notes", "read_notes": "reviews notes",
    "list_files": "browses files", "find_file": "looks for files",
}

# preference phrasing -> verbosity level
_CONCISE = re.compile(r"\b(concise|brief|short|terse|to the point|succinct)\b", re.I)
_DETAILED = re.compile(r"\b(detailed|verbose|thorough|elaborate|in depth|explain)\b", re.I)


@dataclass
class UserProfile:
    preferences: dict[str, Any] = field(default_factory=dict)
    habits: dict[str, Any] = field(default_factory=dict)
    apps_used_most: list[str] = field(default_factory=list)
    verbosity_level: str = "medium"
    active_time_pattern: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class UserModel:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._app_counts: Counter[str] = Counter()
        self._intent_counts: Counter[str] = Counter()
        self._hours = [0] * 24
        self._total = 0
        self.profile = UserProfile()
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        self._app_counts = Counter(data.get("app_counts", {}))
        self._intent_counts = Counter(data.get("intent_counts", {}))
        hours = data.get("hours", [])
        if isinstance(hours, list) and len(hours) == 24:
            self._hours = [int(x) for x in hours]
        self._total = int(data.get("total", 0))
        prof = data.get("profile", {})
        if isinstance(prof, dict):
            self.profile = UserProfile(
                preferences=prof.get("preferences", {}),
                habits=prof.get("habits", {}),
                apps_used_most=prof.get("apps_used_most", []),
                verbosity_level=prof.get("verbosity_level", "medium"),
                active_time_pattern=prof.get("active_time_pattern", ""))

    def _save(self) -> None:
        payload = {
            "app_counts": dict(self._app_counts),
            "intent_counts": dict(self._intent_counts),
            "hours": self._hours, "total": self._total,
            "profile": self.profile.as_dict(),
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- ingestion -----------------------------------------------------------
    def observe(self, intent, success: bool = True, when: datetime | None = None
                ) -> None:
        """Record one executed command and recompute the derived profile."""
        if not success:
            return
        with self._lock:
            when = when or datetime.now()
            self._total += 1
            self._hours[when.hour] += 1
            itype = getattr(intent, "type", "")
            if itype:
                self._intent_counts[itype] += 1
            if itype in ("open_app", "close_app"):
                app = str(getattr(intent, "parameters", {}).get("app", "")).strip().lower()
                if app:
                    self._app_counts[app] += 1
            self._derive()
            self._save()

    def refresh_preferences(self, memory_store) -> None:
        """Pull durable preference facts from the episodic vault (§4.2)."""
        if memory_store is None:
            return
        with self._lock:
            prefs: dict[str, Any] = {}
            verbosity = self.profile.verbosity_level
            try:
                entries = memory_store.all_entries()
            except Exception:
                entries = []
            pref_texts = [e.content for e in entries
                          if getattr(e, "type", "") == "preference"]
            for i, text in enumerate(pref_texts):
                prefs[f"pref_{i+1}"] = text
                if _CONCISE.search(text):
                    verbosity = "low"
                elif _DETAILED.search(text):
                    verbosity = "high"
            self.profile.preferences = prefs
            self.profile.verbosity_level = verbosity
            self._save()

    # -- derivation ----------------------------------------------------------
    def _derive(self) -> None:
        self.profile.apps_used_most = [a for a, _ in self._app_counts.most_common(5)]
        habits: dict[str, Any] = {}
        for itype, n in self._intent_counts.most_common(4):
            phrase = _HABIT_PHRASES.get(itype, itype)
            habits[phrase] = n
        self.profile.habits = habits
        self.profile.active_time_pattern = self._time_pattern()

    def _time_pattern(self) -> str:
        if self._total == 0 or not any(self._hours):
            return "not enough data yet"
        buckets = {"morning (06–12)": sum(self._hours[6:12]),
                   "afternoon (12–18)": sum(self._hours[12:18]),
                   "evening (18–24)": sum(self._hours[18:24]),
                   "night (00–06)": sum(self._hours[0:6])}
        label, count = max(buckets.items(), key=lambda kv: kv[1])
        share = count / max(1, self._total)
        return f"mostly active in the {label} ({share:.0%} of commands)"

    # -- consumption ---------------------------------------------------------
    def hint_for_ai(self) -> str:
        """A short profile summary to bias the AI core's replies (§17 'optimise
        responses'). Empty until there's something worth saying."""
        with self._lock:
            bits: list[str] = []
            if self.profile.verbosity_level != "medium":
                bits.append(f"prefers {self.profile.verbosity_level}-verbosity replies")
            if self.profile.apps_used_most:
                bits.append("frequent apps: " + ", ".join(self.profile.apps_used_most[:3]))
            for text in list(self.profile.preferences.values())[:3]:
                bits.append(str(text))
            return "; ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {"total_commands": self._total, **self.profile.as_dict()}
