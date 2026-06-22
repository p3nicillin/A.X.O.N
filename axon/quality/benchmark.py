"""Deterministic command-routing benchmark with latency and miss reporting."""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from ..ai.context import Context


def load_cases(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = raw.get("cases", []) if isinstance(raw, dict) else []
    return [{"utterance": str(case["utterance"]),
             "intent": str(case["intent"])} for case in cases
            if isinstance(case, dict) and case.get("utterance")
            and case.get("intent")]


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       round((len(ordered) - 1) * quantile))]


def run_benchmark(engine, cases: list[dict]) -> dict:
    latencies = []
    misses = []
    by_intent: dict[str, dict[str, int]] = {}
    context = Context()
    for case in cases:
        started = time.perf_counter()
        packet = engine.interpret(case["utterance"], context)
        latencies.append((time.perf_counter() - started) * 1000)
        expected, actual = case["intent"], packet.intent.type
        bucket = by_intent.setdefault(expected, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if actual == expected:
            bucket["correct"] += 1
        else:
            misses.append({"utterance": case["utterance"],
                           "expected": expected, "actual": actual})
    total = len(cases)
    correct = total - len(misses)
    return {"total": total, "correct": correct,
            "accuracy": round(correct / total if total else 0.0, 4),
            "latency_ms": {
                "median": round(statistics.median(latencies), 3)
                if latencies else 0.0,
                "p95": round(_percentile(latencies, 0.95), 3),
                "max": round(max(latencies), 3) if latencies else 0.0},
            "misses": misses, "by_intent": by_intent}
