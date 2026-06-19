"""The §5 planning engine: IntentPacket -> :class:`Plan`.

Generates a structured plan for a request *before* anything runs. A simple
request is a single tool step; a **compound** request ("open notepad and then
maximise the window") is decomposed into an ordered, bounded multi-step plan
that the executor runs through the skill engine.

Decomposition is deterministic: the source text is split on coordinating
connectors and each clause is re-parsed by an injected ``decomposer`` (the
offline intent engine). A clause that does not resolve to a real tool intent is
ignored, so the planner never invents a step.

Hard rule (§5): the planner never executes anything. It only describes.
"""
from __future__ import annotations

import re

from ..ai.schema import Intent, IntentPacket
from .schema import Plan, PlanStep

# coordinating connectors that join independent commands.
_SPLIT = re.compile(r"\b(?:and then|then|after that|followed by|and also|also|and)\b",
                    re.IGNORECASE)
_NON_TOOL = {"chat", "unknown", "none", ""}


class Planner:
    def __init__(self, decomposer=None, max_steps: int = 5) -> None:
        # decomposer: callable(text) -> Intent | None. Deterministic clause parse.
        self._decompose = decomposer
        self.max_steps = max(1, int(max_steps))

    def build(self, packet: IntentPacket, skill, source_text: str = "") -> Plan:
        intent = packet.intent
        goal = (packet.thought or "").strip() or self._goal_for(intent.type,
                                                                 source_text)

        # conversational / unknown: a zero-tool plan whose success is a reply.
        if not packet.needs_skill:
            return Plan(goal=goal, steps=[],
                        success_criteria="A spoken reply is produced for the user.")

        # compound request? try to decompose into several known tool intents.
        multi = self._decompose_steps(source_text)
        if len(multi) > 1:
            steps = [PlanStep(id=i + 1, action=it.type, tool="",
                              input=dict(it.parameters))
                     for i, it in enumerate(multi)]
            return Plan(goal=goal, steps=steps,
                        success_criteria=f"All {len(steps)} steps complete in order.")

        tool = skill.manifest.name if skill is not None else ""
        step = PlanStep(id=1, action=intent.type, tool=tool,
                        input=dict(intent.parameters))
        success = (f"{tool} completes '{intent.type}' successfully"
                   if tool else f"a capability for '{intent.type}' is available")
        return Plan(goal=goal, steps=[step], success_criteria=success)

    def _decompose_steps(self, source_text: str) -> list[Intent]:
        """Split a compound utterance into ordered tool intents (deterministic).

        Returns [] when no decomposer is configured or fewer than two clauses
        resolve to real tool intents — the caller then keeps the single-step plan.
        """
        if self._decompose is None or not source_text.strip():
            return []
        clauses = [c.strip() for c in _SPLIT.split(source_text) if c and c.strip()]
        if len(clauses) < 2:
            return []
        intents: list[Intent] = []
        for clause in clauses:
            if len(intents) >= self.max_steps:
                break
            intent = self._decompose(clause)
            if intent is not None and intent.type not in _NON_TOOL:
                intents.append(intent)
        return intents if len(intents) > 1 else []

    def _goal_for(self, intent_type: str, source_text: str) -> str:
        if source_text:
            return f"Address the user's request: “{source_text.strip()}”."
        return f"Fulfil intent '{intent_type}'."
