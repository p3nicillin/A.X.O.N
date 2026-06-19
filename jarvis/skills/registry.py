"""Discovers, loads and routes to skills.

Each skill is a sub-package of ``jarvis.skills`` containing:
    manifest.json   -> metadata + declared intents (read for catalogue/router)
    handler.py      -> defines a ``Skill`` subclass named ``SKILL``

The registry imports each handler, validates it against its manifest, and
exposes :meth:`route` which the orchestrator uses to find the right skill for
an intent. This is the only component that knows the full skill set, so adding
a skill is purely additive — drop a folder in and restart.
"""
from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path

from ..ai.schema import Intent, SkillResult
from .base import Skill, SkillManifest


class SkillRegistry:
    def __init__(self) -> None:
        self.skills: list[Skill] = []
        self._by_intent: dict[str, list[Skill]] = {}

    def discover(self) -> "SkillRegistry":
        import jarvis.skills as pkg

        for mod in pkgutil.iter_modules(pkg.__path__):
            if not mod.ispkg:
                continue
            self._load_one(mod.name)
        # index intents for fast routing
        self._by_intent.clear()
        for skill in self.skills:
            for it in skill.manifest.intents:
                self._by_intent.setdefault(it, []).append(skill)
        return self

    def _load_one(self, package: str) -> None:
        base = f"jarvis.skills.{package}"
        manifest_path = Path(__file__).parent / package / "manifest.json"
        if not manifest_path.exists():
            return
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = SkillManifest(
                name=raw["name"], version=raw["version"],
                description=raw["description"], intents=list(raw["intents"]),
                sensitive=bool(raw.get("sensitive", False)),
                author=raw.get("author", "core"),
            )
            handler = importlib.import_module(f"{base}.handler")
            skill: Skill = handler.SKILL
            skill.manifest = manifest          # authoritative metadata
            self.skills.append(skill)
        except Exception as exc:
            print(f"[registry] failed to load skill '{package}': {exc!r}")

    def route(self, intent: Intent) -> Skill | None:
        """Return the first skill that both declares and accepts the intent."""
        for skill in self._by_intent.get(intent.type, ()):
            if skill.can_handle(intent):
                return skill
        return None

    def catalogue(self) -> list[SkillManifest]:
        return [s.manifest for s in self.skills]

    def execute(self, intent: Intent) -> SkillResult:
        skill = self.route(intent)
        if skill is None:
            return SkillResult(
                ok=False, skill="router",
                summary=f"No skill can handle intent '{intent.type}'.",
            )
        try:
            return skill.execute(intent)
        except Exception as exc:  # last-resort sandbox: a skill crash is contained
            return SkillResult(
                ok=False, skill=skill.manifest.name,
                summary=f"Skill '{skill.manifest.name}' errored: {exc}",
            )
