from types import SimpleNamespace

from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine
from axon.ai.schema import Intent
from axon.reasoning.executor import PlanRun
from axon.reasoning.workflows import WorkflowStore
from axon.skills.registry import SkillRegistry


def test_workflow_commands_parse_with_identifiers():
    engine = LocalIntentEngine(SkillRegistry().discover().catalogue())
    assert engine.interpret("list interrupted workflows", Context()).intent.type == "list_workflows"
    resumed = engine.interpret("resume workflow abcdef123456", Context()).intent
    cancelled = engine.interpret("cancel the latest workflow", Context()).intent
    assert resumed.parameters["identifier"] == "abcdef123456"
    assert cancelled.parameters["identifier"] == "latest"


def test_workflow_store_checkpoints_and_restores_without_private_text(tmp_path):
    store = WorkflowStore(tmp_path / "workflows.json")
    steps = [Intent("get_time"), Intent("browser_fill", {
        "field": "email", "text": "private@example.com"})]
    record = store.create("abcdef123456", "fill in private@example.com", steps)

    assert record["resumable"] is False
    raw = (tmp_path / "workflows.json").read_text(encoding="utf-8")
    assert "private@example.com" not in raw
    assert "fill in" not in raw

    result = SimpleNamespace(ok=True, skill="TimeDateSkill", summary="done")
    store.checkpoint("abcdef123456", 1, result)
    saved = store.get("abcdef123456")
    assert saved["index"] == 1
    assert saved["results"][0]["ok"] is True


def test_plan_run_checkpoints_each_advance(tmp_path):
    store = WorkflowStore(tmp_path / "workflows.json")
    steps = [Intent("get_time"), Intent("get_date")]
    store.create("123456abcdef", "", steps)
    run = PlanRun("123456abcdef", steps, True, "", store=store)
    result = SimpleNamespace(ok=True, skill="TimeDateSkill", summary="done")

    run.advance(result)

    assert run.index == 1
    assert store.get("123456abcdef")["index"] == 1


def test_only_safe_interrupted_workflows_are_recoverable(tmp_path):
    store = WorkflowStore(tmp_path / "workflows.json")
    store.create("111111111111", "", [Intent("get_time")])
    store.create("222222222222", "", [Intent("set_clipboard", {"text": "secret"})])
    store.finish("111111111111", "completed")

    assert store.list(recoverable_only=True) == []
