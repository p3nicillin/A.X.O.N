"""§4 memory system: working (in :mod:`jarvis.ai.context`), episodic + semantic.

This package adds the durable half of the cognitive stack:

  * :class:`MemoryEntry` / :class:`MemoryDecision` — the structured records.
  * :class:`MemoryStore`  — episodic markdown vault + semantic vector index.
  * :class:`MemoryGate`   — the decision layer that keeps noise/secrets out.
  * :class:`LocalEmbedder` — dependency-free embeddings (pluggable).
"""
from __future__ import annotations

from .embedding import Embedder, LocalEmbedder, cosine
from .gate import MemoryGate
from .schema import MemoryDecision, MemoryEntry
from .store import MemoryStore

__all__ = [
    "Embedder", "LocalEmbedder", "cosine",
    "MemoryGate", "MemoryDecision", "MemoryEntry", "MemoryStore",
]
