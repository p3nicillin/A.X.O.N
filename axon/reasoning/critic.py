"""The §7 critic: the last gate before execution.

Given a :class:`Plan` and the intent/skill it came from, the critic evaluates
the four §7 dimensions and either approves or blocks:

  * hallucination risk — the plan must reference a real tool for a known intent;
    nothing may be invented (ties to §1 "never assume", §2.4 "don't pretend").
  * missing dependencies — required parameters must be present and non-empty.
  * safety risk — destructive/sensitive actions and sandbox-escaping file paths
    are flagged; path-traversal is blocked outright (§10).
  * logic consistency — the routed tool must actually claim the intent.

Approving with an elevated risk level is fine (the orchestrator's confirmation
gate handles destructive actions); a *block* hard-stops the request.
"""
from __future__ import annotations

import re

from ..ai.schema import IntentPacket
from .schema import Plan, Verdict

# parameters each intent must carry to be executable.
_REQUIRED_PARAMS: dict[str, list[str]] = {
    "open_app": ["app"],
    "close_app": ["app"],
    "open_website": ["site"],
    "search_browser": ["query"],
    "set_timer": ["seconds"],
    "set_reminder": ["seconds", "label"],
    "web_search": ["query"],
    "research_web": ["query"],
    "read_webpage": ["url"],
    "browser_action": ["action"],
    "browser_navigate": ["url"],
    "browser_click": ["target"],
    "browser_fill": ["field", "text"],
    "add_note": ["text"],
    "find_file": ["query"],
    "set_clipboard": ["text"],
    "type_text": ["text"],
    "send_keystroke": ["keys"],
    "read_file": ["path"],
    "write_file": ["path", "text"],
    "create_folder": ["path"],
    "move_path": ["source", "destination"],
    "delete_path": ["path"],
    "calculate": ["expression"],
}

# destructive intents — allowed, but high risk (confirmation gate covers them).
_DESTRUCTIVE = {"close_app", "close_window", "clear_notes", "delete_path"}

# file parameters that must stay inside the sandbox (§10).
_PATH_PARAMS = ("path", "query", "file")
_TRAVERSAL = re.compile(r"(^|[\\/])\.\.([\\/]|$)|^[a-zA-Z]:[\\/]|^[\\/]{2}")


class Critic:
    def __init__(self, known_intents: set[str], min_confidence: float = 0.0
                 ) -> None:
        self.known_intents = set(known_intents)
        self.min_confidence = min_confidence

    def review(self, plan: Plan, packet: IntentPacket, skill) -> Verdict:
        intent = packet.intent
        issues: list[str] = []
        risk = "low"

        # conversational plans (no tool) are always safe to voice.
        if not packet.needs_skill:
            return Verdict.approve(reason="conversational reply, no tool")

        # -- hallucination risk -------------------------------------------
        if skill is None or not plan.tool_steps:
            return Verdict.block(
                [f"no tool implements intent '{intent.type}'"],
                reason="unavailable capability")
        if self.known_intents and intent.type not in self.known_intents:
            return Verdict.block(
                [f"intent '{intent.type}' is not a known capability"],
                reason="unknown intent")

        # -- logic consistency --------------------------------------------
        for step in plan.tool_steps:
            if step.action not in skill.manifest.intents:
                issues.append(
                    f"tool '{step.tool}' does not claim action '{step.action}'")

        # -- missing dependencies -----------------------------------------
        for key in _REQUIRED_PARAMS.get(intent.type, ()):
            val = intent.get(key)
            if val is None or (isinstance(val, str) and not val.strip()):
                issues.append(f"missing required parameter '{key}'")

        # -- safety risk ---------------------------------------------------
        for key in _PATH_PARAMS:
            val = intent.get(key)
            if isinstance(val, str) and _TRAVERSAL.search(val):
                return Verdict.block(
                    [f"parameter '{key}' escapes the sandbox: {val!r}"],
                    reason="sandbox violation")
        if intent.type in _DESTRUCTIVE:
            risk = "high"
        elif skill.manifest.is_sensitive(intent.type):
            risk = "medium"

        # low confidence is a soft signal, not a hard block.
        if self.min_confidence and packet.confidence < self.min_confidence:
            issues.append(f"low confidence ({packet.confidence:.2f})")
            risk = "high" if risk == "low" else risk

        if any(i.startswith(("missing required", "tool '")) for i in issues):
            return Verdict.block(issues, risk_level="high",
                                 reason="plan failed validation")
        return Verdict(approved=True, risk_level=risk, issues=issues,
                       reason="plan validated")

    @staticmethod
    def refusal_phrase(verdict: Verdict) -> str:
        """A concise spoken refusal in the AXON persona."""
        if verdict.issues:
            return f"I can't do that safely, sir — {verdict.issues[0]}."
        return "I'm afraid I can't carry that out safely, sir."
