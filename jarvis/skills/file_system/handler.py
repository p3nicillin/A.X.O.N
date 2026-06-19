"""FileSystemSkill — *restricted* and *read-only*.

Hard sandbox rules (enforced here, not just by convention):
  * Every path is resolved and must stay inside ``data/workspace``. Any attempt
    to escape via ``..`` or absolute paths is refused.
  * No create / write / delete intents exist. The skill can only list, find and
    reveal files in Explorer.
  * Marked ``sensitive`` in the manifest so the orchestrator gates it behind
    user confirmation when ``confirm_sensitive`` is on.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ...ai.schema import Intent, SkillResult
from ...config import DATA_DIR
from ..base import Skill

WORKSPACE = (DATA_DIR / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)


def _safe(rel: str) -> Path | None:
    """Resolve ``rel`` inside WORKSPACE or return None if it escapes."""
    try:
        target = (WORKSPACE / rel).resolve()
    except Exception:
        return None
    if target == WORKSPACE or WORKSPACE in target.parents:
        return target
    return None


class FileSystemSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if intent.type == "list_files":
            return self._list(str(intent.get("path", "")))
        if intent.type == "find_file":
            return self._find(str(intent.get("query", "")))
        if intent.type == "open_folder":
            return self._open(str(intent.get("path", "")))
        return self.fail(f"Unsupported file intent '{intent.type}'.")

    def _list(self, rel: str) -> SkillResult:
        target = _safe(rel)
        if target is None or not target.exists():
            return self.fail("That path is outside the JARVIS workspace.")
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        if not entries:
            return self.ok("The workspace folder is empty.",
                           speak="That folder is empty.")
        summary = ", ".join(entries[:20])
        return self.ok(summary, speak=f"I found {len(entries)} items.",
                       entries=entries)

    def _find(self, query: str) -> SkillResult:
        query = query.strip().lower()
        if not query:
            return self.fail("No filename to search for.")
        hits = [str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*")
                if query in p.name.lower()][:20]
        if not hits:
            return self.ok(f"No files matching '{query}'.",
                           speak=f"I couldn't find anything matching {query}.")
        return self.ok(", ".join(hits), speak=f"I found {len(hits)} matches.",
                       hits=hits)

    def _open(self, rel: str) -> SkillResult:
        target = _safe(rel)
        if target is None or not target.exists():
            return self.fail("That path is outside the JARVIS workspace.")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", str(target)])
        return self.ok(f"Opening {target.name}.", speak="Opening that folder now.")


SKILL = FileSystemSkill()
