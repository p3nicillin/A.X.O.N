from pathlib import Path

from axon.ai.intent_engine import LocalIntentEngine
from axon.quality import load_cases, run_benchmark
from axon.skills.registry import SkillRegistry


def test_checked_in_command_benchmark_meets_quality_gate():
    root = Path(__file__).resolve().parent.parent
    cases = load_cases(root / "benchmarks" / "commands.json")
    engine = LocalIntentEngine(SkillRegistry().discover().catalogue())

    report = run_benchmark(engine, cases)

    assert report["total"] >= 50
    assert report["accuracy"] >= 0.98, report["misses"]
    assert report["latency_ms"]["p95"] < 25
