"""The §5 planning engine: IntentPacket -> :class:`Plan`.

Generates a structured, deterministic plan for a request *before* anything runs.
AXON skills are atomic (one intent → one skill action), so most plans are a
single tool step — but the plan object is the real contract the §7 critic
inspects and the executor follows, and it extends cleanly to multi-step skills.

Hard rule (§5): the planner never executes anything. It only describes.
"""
from __future__ import annotations

from ..ai.schema import IntentPacket
from .schema import Plan, PlanStep


class Planner:
    def build(self, packet: IntentPacket, skill, source_text: str = "") -> Plan:
        intent = packet.intent
        goal = (packet.thought or "").strip() or self._goal_for(intent.type,
                                                                 source_text)

        # conversational / unknown: a zero-tool plan whose success is a reply.
        if not packet.needs_skill:
            return Plan(goal=goal, steps=[],
                        success_criteria="A spoken reply is produced for the user.")

        tool = skill.manifest.name if skill is not None else ""
        step = PlanStep(id=1, action=intent.type, tool=tool,
                        input=dict(intent.parameters))
        success = (f"{tool} completes '{intent.type}' successfully"
                   if tool else f"a capability for '{intent.type}' is available")
        return Plan(goal=goal, steps=[step], success_criteria=success)

    def _goal_for(self, intent_type: str, source_text: str) -> str:
        if source_text:
            return f"Address the user's request: “{source_text.strip()}”."
        return f"Fulfil intent '{intent_type}'."
