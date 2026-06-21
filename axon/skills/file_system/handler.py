"""FileSystemSkill — useful file work inside a hard workspace sandbox.

Hard sandbox rules (enforced here, not just by convention):
  * Every path is resolved and must stay inside ``data/workspace``. Any attempt
    to escape via ``..`` or absolute paths is refused.
  * Reads are bounded and binary files are refused.
  * Mutations are declared per-intent sensitive and are confirmation-gated.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...ai.schema import Intent, SkillResult
from ...config import DATA_DIR
from ..base import Skill

WORKSPACE = (DATA_DIR / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
_MAX_READ = 64 * 1024
_MAX_WRITE = 100 * 1024


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
        if intent.type == "read_file":
            return self._read(str(intent.get("path", "")))
        if intent.type == "write_file":
            return self._write(str(intent.get("path", "")),
                               str(intent.get("text", "")),
                               bool(intent.get("append", False)))
        if intent.type == "create_folder":
            return self._mkdir(str(intent.get("path", "")))
        if intent.type == "move_path":
            return self._move(str(intent.get("source", "")),
                              str(intent.get("destination", "")))
        if intent.type == "delete_path":
            return self._delete(str(intent.get("path", "")))
        if intent.type == "open_folder":
            return self._open(str(intent.get("path", "")))
        return self.fail(f"Unsupported file intent '{intent.type}'.")

    def _list(self, rel: str) -> SkillResult:
        target = _safe(rel)
        if target is None or not target.exists():
            return self.fail("That path is outside the AXON workspace.")
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

    def _read(self, rel: str) -> SkillResult:
        target = _safe(rel.strip())
        if target is None or not target.is_file():
            return self.fail("That file does not exist inside the workspace.")
        if target.stat().st_size > _MAX_READ:
            return self.fail("That file exceeds the 64 kilobyte read limit.")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return self.fail("That appears to be a binary file.")
        relative = str(target.relative_to(WORKSPACE))
        preview = text[:2000]
        return self.ok(f"{relative}: {preview}",
                       speak=f"I read {relative}. It contains {len(text)} characters.",
                       path=relative, text=preview, truncated=len(text) > len(preview))

    def _write(self, rel: str, text: str, append: bool) -> SkillResult:
        target = _safe(rel.strip())
        if target is None or target == WORKSPACE or not target.name:
            return self.fail("A file path inside the workspace is required.")
        if not target.parent.is_dir():
            return self.fail("The destination folder does not exist.")
        existing = ""
        if append and target.exists():
            if not target.is_file() or target.stat().st_size > _MAX_WRITE:
                return self.fail("The existing file cannot be appended safely.")
            try:
                existing = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return self.fail("Binary files cannot be modified.")
        content = existing + text
        if not text or len(content.encode("utf-8")) > _MAX_WRITE:
            return self.fail("Text must contain 1 to 102400 UTF-8 bytes.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=target.parent,
                    prefix=".axon-", suffix=".tmp", delete=False) as handle:
                handle.write(content)
                temporary = Path(handle.name)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        relative = str(target.relative_to(WORKSPACE))
        action = "Appended to" if append else "Wrote"
        return self.ok(f"{action} {relative}.", speak=f"{action} {relative}, sir.",
                       path=relative, bytes=len(content.encode("utf-8")))

    def _mkdir(self, rel: str) -> SkillResult:
        target = _safe(rel.strip())
        if target is None or target == WORKSPACE:
            return self.fail("A new folder path inside the workspace is required.")
        if target.exists():
            return self.fail("That folder or file already exists.")
        target.mkdir(parents=False)
        relative = str(target.relative_to(WORKSPACE))
        return self.ok(f"Created folder {relative}.",
                       speak=f"Created {relative}, sir.", path=relative)

    def _move(self, source_rel: str, destination_rel: str) -> SkillResult:
        source, destination = _safe(source_rel.strip()), _safe(destination_rel.strip())
        if (source is None or destination is None or source == WORKSPACE
                or destination == WORKSPACE or not source.exists()):
            return self.fail("Both paths must be valid workspace locations.")
        if destination.exists() or not destination.parent.is_dir():
            return self.fail("The destination exists or its folder is missing.")
        shutil.move(str(source), str(destination))
        relative = str(destination.relative_to(WORKSPACE))
        return self.ok(f"Moved to {relative}.", speak=f"Moved it to {relative}, sir.",
                       source=source_rel, destination=relative)

    def _delete(self, rel: str) -> SkillResult:
        target = _safe(rel.strip())
        if target is None or target == WORKSPACE or not target.exists():
            return self.fail("That workspace path cannot be deleted.")
        if target.is_dir():
            try:
                target.rmdir()  # deliberately refuses non-empty directories
            except OSError:
                return self.fail("Only empty folders can be deleted.")
        else:
            target.unlink()
        relative = str(target.relative_to(WORKSPACE))
        return self.ok(f"Deleted {relative}.", speak=f"Deleted {relative}, sir.",
                       path=relative)

    def _open(self, rel: str) -> SkillResult:
        target = _safe(rel)
        if target is None or not target.exists():
            return self.fail("That path is outside the AXON workspace.")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", str(target)])
        return self.ok(f"Opening {target.name}.", speak="Opening that folder now.")


SKILL = FileSystemSkill()
