"""Run the checked-in command corpus as a CI quality gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from axon.ai.intent_engine import LocalIntentEngine  # noqa: E402
from axon.quality import load_cases, run_benchmark  # noqa: E402
from axon.skills.registry import SkillRegistry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path,
                        default=ROOT / "benchmarks" / "commands.json")
    parser.add_argument("--minimum", type=float, default=0.98)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = load_cases(args.corpus)
    engine = LocalIntentEngine(SkillRegistry().discover().catalogue())
    report = run_benchmark(engine, cases)
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["accuracy"] >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
