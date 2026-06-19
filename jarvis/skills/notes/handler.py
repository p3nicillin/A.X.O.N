"""NotesSkill — append-only local memory in the §4 markdown entry format.

Stored at ``data/notes.md`` as repeated blocks:

    # Entry

    Timestamp: 2026-06-18T23:10:00
    Content: buy milk
    Tags: shopping

This is JARVIS's *only* memory source (§4): no long-term reasoning memory is
assumed or fabricated.
"""
from __future__ import annotations

import re
from datetime import datetime

from ...ai.schema import Intent, SkillResult
from ...config import DATA_DIR
from ..base import Skill

NOTES_FILE = DATA_DIR / "notes.md"
_CONTENT_RE = re.compile(r"^Content:\s*(.*)$", re.MULTILINE)


class NotesSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        if intent.type == "add_note":
            return self._add(intent)
        if intent.type == "clear_notes":
            NOTES_FILE.write_text("", encoding="utf-8")
            return self.ok("All notes cleared.", speak="All notes cleared, sir.")
        return self._read()

    def _add(self, intent: Intent) -> SkillResult:
        text = str(intent.get("text", "")).strip()
        if not text:
            return self.fail("There was nothing to note down, sir.")
        tags = intent.get("tags", "")
        if isinstance(tags, (list, tuple)):
            tags = ", ".join(str(t) for t in tags)
        entry = (
            "# Entry\n\n"
            f"Timestamp: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Content: {text}\n"
            f"Tags: {tags}\n\n"
        )
        with NOTES_FILE.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        return self.ok(f"Noted: {text}", speak="I've added that to your notes, sir.")

    def _read(self) -> SkillResult:
        if not NOTES_FILE.exists() or not NOTES_FILE.read_text(encoding="utf-8").strip():
            return self.ok("No notes yet.", speak="You have no notes, sir.")
        contents = _CONTENT_RE.findall(NOTES_FILE.read_text(encoding="utf-8"))
        if not contents:
            return self.ok("No notes yet.", speak="You have no notes, sir.")
        recent = contents[-5:]
        spoken = "Your latest notes are: " + "; ".join(recent) + ", sir."
        summary = " | ".join(recent)
        return self.ok(summary, speak=spoken, count=len(contents))


SKILL = NotesSkill()
