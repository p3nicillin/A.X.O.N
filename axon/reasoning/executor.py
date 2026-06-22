"""The §5 executor: runs a multi-step :class:`Plan` through the skill engine.

The executor is the *only* place a multi-step plan turns into actions, and it
acts solely through :class:`SkillRegistry` — it never touches the OS directly,
exactly like a single skill call. Each step is gated by the §7 critic before it
runs, every step in one plan shares a single correlation id in the audit trail,
and a failed or blocked step aborts the rest of the plan.

Confirmation of a sensitive/destructive step is *not* handled here: that needs
an async yes/no from the user, so the orchestrator owns the pause/resume. The
executor only decides (review) and acts (run_step); the orchestrator sequences.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from ..ai.schema import Intent, IntentPacket, SkillResult
from ..core.event_bus import Event
from .schema import Verdict


@dataclass
class PlanRun:
    """Mutable progress state for one multi-step plan in flight."""
    correlation: str
    steps: list[Intent]
    wake: bool
    source_text: str
    index: int = 0
    results: list[SkillResult] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    store: object | None = field(default=None, repr=False)

    @property
    def current(self) -> Intent | None:
        return self.steps[self.index] if self.index < len(self.steps) else None

    @property
    def done(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    def advance(self, result: SkillResult) -> None:
        self.results.append(result)
        self.index += 1
        if self.store is not None:
            self.store.checkpoint(self.correlation, self.index, result)


def _packet_for(intent: Intent, source_text: str = "") -> IntentPacket:
    return IntentPacket(thought="", intent=intent, response_text="",
                        source_text=source_text, needs_skill=True, confidence=1.0)


class Executor:
    def __init__(self, registry, critic, planner, bus, command_log,
                 *, timeout: float = 15.0, workflow_store=None) -> None:
        self._registry = registry
        self._critic = critic
        self._planner = planner
        self._bus = bus
        self._command_log = command_log      # callable from the orchestrator
        self.timeout = max(1.0, float(timeout))
        self.workflow_store = workflow_store

    def new_run(self, plan, source_text: str, wake: bool) -> PlanRun:
        steps = [Intent(type=s.action, parameters=dict(s.input))
                 for s in plan.steps]
        run = PlanRun(correlation=uuid.uuid4().hex[:12], steps=steps,
                      wake=wake, source_text=source_text,
                      store=self.workflow_store)
        if self.workflow_store is not None:
            self.workflow_store.create(run.correlation, source_text, steps)
        return run

    def restore_run(self, correlation: str, *, wake: bool = True) -> PlanRun | None:
        if self.workflow_store is None:
            return None
        record = self.workflow_store.get(correlation)
        if not record or record.get("status") != "running" or not record.get("resumable"):
            return None
        steps = [Intent(type=s["type"], parameters=dict(s.get("parameters", {})))
                 for s in record.get("steps", [])]
        index = max(0, min(int(record.get("index", 0)), len(steps)))
        results = [SkillResult(ok=bool(r.get("ok")),
                               skill=str(r.get("skill", "workflow")),
                               summary=str(r.get("summary", "completed")))
                   for r in record.get("results", [])[:index]]
        return PlanRun(correlation=correlation, steps=steps, wake=wake,
                       source_text=str(record.get("source", "")), index=index,
                       results=results, store=self.workflow_store)

    def finish(self, run: PlanRun, status: str = "completed") -> None:
        if self.workflow_store is not None:
            self.workflow_store.finish(run.correlation, status)

    def timed_out(self, run: PlanRun) -> bool:
        return (time.monotonic() - run.started) > self.timeout

    def review(self, intent: Intent):
        """Route + critique a single step. Returns (Verdict, skill)."""
        skill = self._registry.route(intent)
        packet = _packet_for(intent)
        plan = self._planner.build(packet, skill, "")
        if self._critic is None:
            verdict = Verdict.approve(reason="critic disabled")
        else:
            verdict = self._critic.review(plan, packet, skill)
        return verdict, skill

    def run_step(self, intent: Intent, run: PlanRun) -> SkillResult:
        """Execute one step through the registry and audit it with the run's id."""
        result = self._registry.execute(intent)
        self._bus.publish(Event.SKILL_RESULT, result)
        self._command_log(run.wake, intent.command_type, intent.type,
                           result.skill, result.ok, run.correlation)
        return result

    def log_blocked(self, intent: Intent, run: PlanRun) -> None:
        """Audit a step that the critic blocked (or that had no skill)."""
        self._command_log(run.wake, intent.command_type, intent.type,
                           "none", False, run.correlation)
