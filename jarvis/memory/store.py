"""The §4.2 + §4.3 persistent memory store.

Layout under ``data/memory/``::

    episodic/<id>.md     one human-readable markdown vault file per fact
    semantic.json        the vector index { id -> {vector, meta} }

The vault is the source of truth (Obsidian-readable, hand-editable). The
semantic index is a derived cache: if it is missing or stale it is rebuilt from
the vault on startup, so deleting ``semantic.json`` never loses data.

Storing a fact is idempotent on :attr:`MemoryEntry.id` (content-derived), so the
same fact restored twice updates in place rather than duplicating — this is what
keeps the "never hallucinate / never duplicate memory" rule (§1) honest.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .embedding import Embedder, LocalEmbedder, cosine
from .schema import MemoryEntry

# (entry, score) pairs returned by recall
Recall = tuple[MemoryEntry, float]


class MemoryStore:
    def __init__(self, root: Path, embedder: Embedder | None = None) -> None:
        self.root = root
        self.vault = root / "episodic"
        self.index_path = root / "semantic.json"
        self.vault.mkdir(parents=True, exist_ok=True)
        self.embedder: Embedder = embedder or LocalEmbedder()
        self._lock = threading.RLock()
        # id -> {"vector": [...], "meta": {...}}
        self._index: dict[str, dict] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            loaded = self._load_index_file()
            if loaded is not None and loaded.get("dim") == self.embedder.dim:
                self._index = loaded.get("entries", {})
                # reconcile against the vault in case files were added/removed
                self._reconcile()
            else:
                # missing, corrupt, or embedder dimension changed -> rebuild
                self._rebuild_from_vault()

    def _load_index_file(self) -> dict | None:
        if not self.index_path.exists():
            return None
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_index(self) -> None:
        payload = {"dim": self.embedder.dim, "entries": self._index}
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.index_path)        # atomic on the same filesystem

    def _rebuild_from_vault(self) -> None:
        self._index = {}
        for path in sorted(self.vault.glob("*.md")):
            entry = MemoryEntry.from_markdown(
                path.read_text(encoding="utf-8"), entry_id=path.stem)
            if entry is not None:
                self._index[entry.id] = self._record(entry)
        self._save_index()

    def _reconcile(self) -> None:
        on_disk = {p.stem for p in self.vault.glob("*.md")}
        indexed = set(self._index)
        changed = False
        for missing in on_disk - indexed:          # vault file w/o index entry
            entry = MemoryEntry.from_markdown(
                (self.vault / f"{missing}.md").read_text(encoding="utf-8"),
                entry_id=missing)
            if entry is not None:
                self._index[entry.id] = self._record(entry)
                changed = True
        for stale in indexed - on_disk:             # index entry w/o vault file
            self._index.pop(stale, None)
            changed = True
        if changed:
            self._save_index()

    def _record(self, entry: MemoryEntry) -> dict:
        return {"vector": self.embedder.embed(entry.content),
                "meta": entry.as_dict()}

    # -- public API ----------------------------------------------------------
    def remember(self, entry: MemoryEntry) -> MemoryEntry:
        """Persist a fact to the vault and (re)index it. Idempotent on id."""
        with self._lock:
            (self.vault / f"{entry.id}.md").write_text(
                entry.to_markdown(), encoding="utf-8")
            self._index[entry.id] = self._record(entry)
            self._save_index()
            return entry

    def recall(self, query: str, k: int = 3,
               min_score: float = 0.18) -> list[Recall]:
        """Top-k vault entries by semantic similarity to ``query``."""
        with self._lock:
            if not self._index:
                return []
            qv = self.embedder.embed(query)
            scored: list[Recall] = []
            for rec in self._index.values():
                score = cosine(qv, rec["vector"])
                if score >= min_score:
                    scored.append((MemoryEntry(**rec["meta"]), score))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return scored[:k]

    def forget(self, entry_id: str) -> bool:
        with self._lock:
            removed = self._index.pop(entry_id, None) is not None
            path = self.vault / f"{entry_id}.md"
            if path.exists():
                path.unlink()
                removed = True
            if removed:
                self._save_index()
            return removed

    def all_entries(self) -> list[MemoryEntry]:
        with self._lock:
            return [MemoryEntry(**rec["meta"]) for rec in self._index.values()]

    def stats(self) -> dict:
        with self._lock:
            by_type: dict[str, int] = {}
            for rec in self._index.values():
                t = rec["meta"].get("type", "knowledge")
                by_type[t] = by_type.get(t, 0) + 1
            return {"count": len(self._index), "by_type": by_type,
                    "dim": self.embedder.dim}
