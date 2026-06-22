"""Catalogue endpoint for workflow controls; orchestration owns actual resume."""
from __future__ import annotations

from ...ai.schema import Intent, SkillResult
from ...config import DATA_DIR
from ...reasoning.workflows import WorkflowStore
from ..base import Skill


class WorkflowControlSkill(Skill):
    def __init__(self) -> None:
        self.store = WorkflowStore(DATA_DIR / "workflows.json")

    def execute(self, intent: Intent) -> SkillResult:
        identifier = str(intent.get("identifier", "")).strip().lower()
        recoverable = self.store.list(recoverable_only=True)
        if intent.type == "list_workflows":
            return self.ok(f"{len(recoverable)} recoverable workflow(s).",
                           workflows=recoverable[:10], count=len(recoverable))
        if identifier in {"", "last", "latest"} and recoverable:
            identifier = str(recoverable[0]["id"])
        if intent.type == "cancel_workflow":
            if self.store.cancel(identifier):
                return self.ok(f"Workflow {identifier} cancelled.", identifier=identifier)
            return self.fail("No active workflow matched that ID.")
        if intent.type == "resume_workflow":
            # The Orchestrator intercepts this intent and re-applies critic and
            # confirmation gates. A direct skill call must never bypass them.
            return self.fail("Workflow resume must run through the orchestrator.")
        return self.fail(f"Unsupported workflow action '{intent.type}'.")


SKILL = WorkflowControlSkill()
