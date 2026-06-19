"""§16 Phase-3 autonomy: read-only awareness + controlled proactive suggestions.

    ContextSensor (§16.1)  ->  ContextEvent stream (§16.2)
                                      │
                       AutonomyEngine (§16.3/16.4 loop)
                                      │  + TaskScheduler (§16.5)
                                      ▼
                              Suggestion (advice only — never acts)

Opt-in via config.autonomy_enabled. Everything here observes and suggests; it
never executes a skill or changes state.
"""
from __future__ import annotations

from .context import ContextSensor, ContextSnapshot
from .engine import AutonomyEngine
from .events import ContextEvent, Suggestion
from .tasks import Task, TaskScheduler

__all__ = ["ContextSensor", "ContextSnapshot", "AutonomyEngine",
           "ContextEvent", "Suggestion", "Task", "TaskScheduler"]
