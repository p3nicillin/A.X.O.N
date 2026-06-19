"""§5 planning + §7 critic: the deliberation layer between intent and action.

    intent  ->  Planner.build()  ->  Plan  ->  Critic.review()  ->  Verdict
                                                                       │
                                          approved ─────────────► execute
                                          blocked  ─────────────► refuse

Both layers are deterministic and never execute anything themselves.
"""
from __future__ import annotations

from .critic import Critic
from .planner import Planner
from .schema import Plan, PlanStep, Verdict

__all__ = ["Critic", "Planner", "Plan", "PlanStep", "Verdict"]
