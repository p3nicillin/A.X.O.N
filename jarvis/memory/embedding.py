"""Text -> vector, for the §4.3 semantic memory.

Two interchangeable backends behind one interface, mirroring the AI core's
"cloud-best, local-always" pattern:

  * ``LocalEmbedder``  — a deterministic, dependency-free embedding: tokens and
    character tri-grams are hashed into a fixed-width vector with sublinear term
    weighting, then L2-normalised. No model download, no network, works on any
    interpreter. This is a *lexical-semantic* index: it captures word and
    sub-word overlap (so "open the browser" recalls "launch chrome browser"),
    not deep paraphrase. It is the always-available fallback.

  * Any object implementing :meth:`Embedder.embed` may replace it (e.g. a real
    sentence-transformer) once wheels exist for the target interpreter — the
    store only depends on the :class:`Embedder` protocol below.

Heavy ML stacks (torch / sentence-transformers / faiss) have no wheels on the
project's Python build, which is why the default is pure-Python.
"""
from __future__ import annotations

import math
import re
from typing import Protocol

_WORD_RE = re.compile(r"[a-z0-9]+")
# very common words carry little retrieval signal; drop them so similarity is
# driven by the meaningful terms.
_STOP = frozenset(
    "the a an and or but of to in on at for is are was were be been it this that "
    "i you he she they we me my your his her our their do does did so as if then "
    "with from by about into over under can could would should will shall may "
    "please sir".split()
)


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]:
        ...


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]


class LocalEmbedder:
    """Hashed bag-of-features embedding. Deterministic and offline."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _bucket(self, feature: str) -> int:
        # stable hash independent of PYTHONHASHSEED (Python's str hash is salted)
        h = 1469598103934665603
        for byte in feature.encode("utf-8"):
            h = ((h ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        return h % self.dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        words = _tokens(text)
        if not words:
            return vec
        # term frequencies with sublinear (log) damping
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
            # character tri-grams give robustness to plurals/typos/morphology
            padded = f"#{w}#"
            for i in range(len(padded) - 2):
                tri = padded[i:i + 3]
                counts["~" + tri] = counts.get("~" + tri, 0) + 1
        for feature, n in counts.items():
            weight = 1.0 + math.log(n)
            # tri-gram features get down-weighted vs whole words
            if feature.startswith("~"):
                weight *= 0.35
            vec[self._bucket(feature)] += weight
        return _normalise(vec)


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (assumed L2-normalised,
    but we divide defensively in case a caller passes raw vectors)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
