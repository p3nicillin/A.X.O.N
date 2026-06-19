"""Structured records for the §5 planning engine and §7 critic.

  * :class:`Plan` / :class:`PlanStep` — the §5 plan shape ({goal, steps[],
    success_criteria}) produced *before* any execution.
  * :class:`Verdict` — the §7 critic's ruling: approve or block, with a risk
    level and the specific issues found.

Pure data — no execution, no I/O. The planner and critic operate on these and
the orchestrator acts on the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RISK_LEVELS = ("low", "medium", "high")


@dataclass
class PlanStep:
    id: int
    action: str                 # the verb / intent type, e.g. "open_app"
    tool: str                   # skill that will run it, "" for none
    input: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "action": self.action,
                "tool": self.tool, "input": self.input}


@dataclass
class Plan:
    """The §5 deterministic plan generated for a request before execution."""
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    success_criteria: str = ""

    @property
    def tool_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.tool]

    def as_dict(self) -> dict[str, Any]:
        return {"goal": self.goal,
                "steps": [s.as_dict() for s in self.steps],
                "success_criteria": self.success_criteria}

    def summary(self) -> str:
        if not self.steps:
            return f"goal={self.goal!r} (no tool steps)"
        chain = " -> ".join(f"{s.action}[{s.tool or 'none'}]" for s in self.steps)
        return f"goal={self.goal!r} steps={chain}"


@dataclass
class Verdict:
    """The §7 critic ruling. ``approved`` False means execution is blocked."""
    approved: bool
    risk_level: str = "low"            # one of RISK_LEVELS
    issues: list[str] = field(default_factory=list)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.risk_level not in RISK_LEVELS:
            self.risk_level = "low"

    def as_dict(self) -> dict[str, Any]:
        return {"approved": self.approved, "risk_level": self.risk_level,
                "issues": list(self.issues), "reason": self.reason}

    def summary(self) -> str:
        state = "APPROVE" if self.approved else "BLOCK"
        extra = f" — {'; '.join(self.issues)}" if self.issues else ""
        return f"{state} (risk={self.risk_level}){extra}"

    @classmethod
    def approve(cls, risk_level: str = "low", reason: str = "") -> "Verdict":
        return cls(approved=True, risk_level=risk_level, reason=reason)

    @classmethod
    def block(cls, issues: list[str], risk_level: str = "high",
              reason: str = "") -> "Verdict":
        return cls(approved=False, risk_level=risk_level, issues=issues,
                   reason=reason)
