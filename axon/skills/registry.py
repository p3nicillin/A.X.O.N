"""Discovers, loads and routes to skills.

Each skill is a sub-package of ``axon.skills`` containing:
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
    def __init__(self, disabled: list[str] | None = None, config=None) -> None:
        self.skills: list[Skill] = []
        self._by_intent: dict[str, list[Skill]] = {}
        self._disabled = {s.lower() for s in (disabled or [])}
        self._config = config

    def discover(self) -> "SkillRegistry":
        import axon.skills as pkg

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
        base = f"axon.skills.{package}"
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
                intent_params={k: list(v) for k, v in
                               dict(raw.get("intent_params", {})).items()},
                sensitive_intents=list(raw.get("sensitive_intents", [])),
            )
            unknown_sensitive = set(manifest.sensitive_intents) - set(manifest.intents)
            if unknown_sensitive:
                raise ValueError("sensitive_intents must be declared intents: "
                                 + ", ".join(sorted(unknown_sensitive)))
            handler = importlib.import_module(f"{base}.handler")
            skill: Skill = handler.SKILL
            skill.manifest = manifest          # authoritative metadata
            if self._config is not None and hasattr(skill, "configure"):
                skill.configure(self._config)
            self.skills.append(skill)
        except Exception as exc:
            print(f"[registry] failed to load skill '{package}': {exc!r}")

    def route(self, intent: Intent) -> Skill | None:
        """Return the first skill that both declares and accepts the intent."""
        for skill in self._by_intent.get(intent.type, ()):
            if self.is_enabled(skill.manifest.name) and skill.can_handle(intent):
                return skill
        return None

    def catalogue(self) -> list[SkillManifest]:
        return [s.manifest for s in self.skills]

    def is_enabled(self, name: str) -> bool:
        return name.lower() not in self._disabled

    def set_enabled(self, name: str, enabled: bool) -> bool:
        known = {s.manifest.name.lower(): s.manifest.name for s in self.skills}
        key = name.lower()
        if key not in known:
            return False
        if enabled:
            self._disabled.discard(key)
        else:
            self._disabled.add(key)
        return True

    def disabled_skills(self) -> list[str]:
        known = {s.manifest.name.lower(): s.manifest.name for s in self.skills}
        return sorted(known[k] for k in self._disabled if k in known)

    def execute(self, intent: Intent) -> SkillResult:
        skill = self.route(intent)
        if skill is None:
            return SkillResult(
                ok=False, skill="router",
                summary=f"No skill can handle intent '{intent.type}'.",
            )
        allowed = set(skill.manifest.params_for(intent.type))
        unknown = set(intent.parameters) - allowed
        if unknown:
            return SkillResult(
                ok=False, skill=skill.manifest.name,
                summary=(f"Intent '{intent.type}' contains unsupported "
                         f"parameter(s): {', '.join(sorted(unknown))}."),
            )
        try:
            return skill.execute(intent)
        except Exception as exc:  # last-resort sandbox: a skill crash is contained
            return SkillResult(
                ok=False, skill=skill.manifest.name,
                summary=f"Skill '{skill.manifest.name}' errored: {exc}",
            )
