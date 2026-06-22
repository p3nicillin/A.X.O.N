"""Phase-2 AI-core tests: the IntentBackend contract, schema generation/repair,
backend selection + fallback chain, hybrid fast-path, and the circuit breaker.

A FakeRuntime stands in for a local LLM server so these run with no network and
no model installed.
"""
import json

import pytest

from axon.ai.backends.base import (IntentBackendError, IntentSpec, all_specs,
                                      specs_from_catalogue)
from axon.ai.backends.local_llm import LocalLLMBackend
from axon.ai.backends.rules import RuleBackend
from axon.ai.backends.schema_gen import (build_json_schema, build_system_prompt,
                                           validate_packet_dict)
from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine, _resolve_chain, build_engine
from axon.ai.router import IntentRouter, _Breaker
from axon.ai.schema import IntentPacket
from axon.config import Config
from axon.skills.registry import SkillRegistry

CATALOGUE = SkillRegistry().discover().catalogue()
SPECS = all_specs(CATALOGUE)


# --- a controllable fake local runtime --------------------------------------
class FakeRuntime:
    def __init__(self, replies, healthy=True, model="fake-model"):
        self._replies = list(replies)
        self.calls = 0
        self.model = model
        self._healthy = healthy

    def health(self, timeout=1.5):
        return (self._healthy, "fake")

    def chat(self, messages, *, schema=None, temperature=0.1, max_tokens=256,
             timeout=4.0):
        self.calls += 1
        if not self._replies:
            return ""
        return self._replies.pop(0)

    def warm(self, timeout=30.0):
        return True


def local_backend(replies, healthy=True):
    b = LocalLLMBackend(Config(), runtime=FakeRuntime(replies, healthy))
    return b


VALID = json.dumps({"thought": "t", "intent": {"type": "get_time",
                    "parameters": {}}, "response_text": "now", "confidence": 0.9})


# --- schema generation + validation -----------------------------------------
def test_schema_enum_is_sourced_from_registry():
    schema = build_json_schema(SPECS)
    enum = schema["properties"]["intent"]["properties"]["type"]["enum"]
    assert "get_time" in enum and "open_app" in enum
    assert "chat" in enum and "answer" in enum and "unknown" in enum
    # a skill that doesn't exist must not appear
    assert "teleport" not in enum


def test_prompt_lists_params_and_unknown_escape_hatch():
    prompt = build_system_prompt(SPECS)
    assert "open_app" in prompt and "app" in prompt
    assert "unknown" in prompt          # the safe escape hatch is described


def test_active_window_context_is_bounded_and_explicit():
    context = Context()
    context.set_desktop_hint("Active window title: Project - Visual Studio Code")
    prompt = local_backend([VALID])._system(SPECS, context)

    assert "Current desktop context" in prompt
    assert "Project - Visual Studio Code" in prompt


def test_validate_rejects_unknown_intent_and_params():
    ok, _ = validate_packet_dict(json.loads(VALID), SPECS)
    assert ok
    bad_type = {"intent": {"type": "teleport", "parameters": {}}}
    assert validate_packet_dict(bad_type, SPECS)[0] is False
    bad_param = {"intent": {"type": "get_time", "parameters": {"x": 1}}}
    assert validate_packet_dict(bad_param, SPECS)[0] is False


# --- contract: every backend returns a valid packet OR raises ----------------
def test_rule_backend_contract_never_raises():
    rb = RuleBackend(LocalIntentEngine(CATALOGUE))
    p = rb.parse("what time is it", Context(), SPECS)
    assert isinstance(p, IntentPacket) and p.intent.type == "get_time"
    # even gibberish yields a valid (unknown) packet — it is the floor
    assert rb.parse("asdfqwer", Context(), SPECS).intent.type == "unknown"


def test_local_backend_returns_valid_packet():
    b = local_backend([VALID])
    p = b.parse("what time is it", Context(), SPECS)
    assert isinstance(p, IntentPacket)
    assert p.intent.type == "get_time"
    assert p.repaired is False


def test_local_backend_repairs_once_then_succeeds():
    b = local_backend(["not json at all", VALID])
    p = b.parse("what time is it", Context(), SPECS)
    assert p.intent.type == "get_time"
    assert p.repaired is True
    assert b.runtime.calls == 2          # one bad + one repair


def test_local_backend_raises_when_unrepairable():
    b = local_backend(["garbage", "still garbage"])
    with pytest.raises(IntentBackendError):
        b.parse("what time is it", Context(), SPECS)


def test_local_backend_unavailable_when_runtime_down():
    b = local_backend([VALID], healthy=False)
    assert b.available() is False


# --- selection + fallback chain ---------------------------------------------
def test_resolve_chain_variants():
    cfg = Config()
    cfg.ai.engine = "local"
    assert _resolve_chain(cfg, cloud_enabled=False) == ["local"]
    cfg.ai.engine = "rules"
    assert _resolve_chain(cfg, cloud_enabled=False) == []
    cfg.ai.engine = "cloud"
    assert _resolve_chain(cfg, cloud_enabled=True) == ["cloud", "local"]
    cfg.ai.engine = "auto"
    assert _resolve_chain(cfg, cloud_enabled=True) == ["cloud", "local"]
    assert _resolve_chain(cfg, cloud_enabled=False) == ["local"]


def _router(local, *, hybrid=True, chain=("local",)):
    rb = RuleBackend(LocalIntentEngine(CATALOGUE))
    return IntentRouter(Config(), CATALOGUE, None,
                        backends={"local": local}, rule_backend=rb,
                        chain=list(chain), hybrid=hybrid)


def test_router_uses_local_when_healthy():
    r = _router(local_backend([VALID]), hybrid=False)
    p = r.interpret("please tell me the hour somehow", Context())
    assert p.backend == "local" and p.model == "fake-model"
    assert p.latency_ms >= 0


def test_router_falls_back_to_rules_when_backend_fails():
    r = _router(local_backend(["garbage", "garbage"]), hybrid=False)
    p = r.interpret("what time is it", Context())
    assert p.backend == "rules"
    assert r.metrics.fallback_to_rules == 1


def test_router_skips_unavailable_backend():
    r = _router(local_backend([VALID], healthy=False), hybrid=False)
    p = r.interpret("what time is it", Context())
    assert p.backend == "rules"


def test_hybrid_fast_path_skips_the_llm():
    lb = local_backend([VALID])
    r = _router(lb, hybrid=True)
    p = r.interpret("what time is it", Context())
    assert p.backend == "rules-fastpath"
    assert lb.runtime.calls == 0           # the LLM was never consulted
    assert r.metrics.fast_path_hits == 1


def test_non_fastpath_still_uses_llm():
    lb = local_backend([json.dumps({"thought": "t", "intent": {
        "type": "web_search", "parameters": {"query": "x"}},
        "response_text": "ok", "confidence": 0.9})])
    r = _router(lb, hybrid=True)
    # a free-text query is not a fast-path intent -> escalates to the LLM
    p = r.interpret("ponder the meaning of the number 42 please", Context())
    assert p.backend == "local"
    assert lb.runtime.calls == 1


# --- circuit breaker ---------------------------------------------------------
def test_breaker_opens_after_threshold_and_recovers():
    b = _Breaker(threshold=2, cooldown=999)
    assert b.allow()
    b.record_failure(); assert b.allow()
    b.record_failure(); assert b.allow() is False    # open
    b.record_success(); assert b.allow() is True     # reset


# --- build_engine integration -----------------------------------------------
def test_build_engine_returns_router_with_rules_floor():
    cfg = Config()                       # engine=local, no runtime present
    r = build_engine(cfg, CATALOGUE)
    assert isinstance(r, IntentRouter)
    # health resolves to rules when the local runtime is unreachable
    h = r.health()
    assert h["active"] in ("rules", "local")
    # interpret always returns a valid packet regardless
    p = r.interpret("teleport me to mars", Context())
    assert p.intent.type in ("unknown", "web_search")


def test_build_engine_applies_nested_local_config():
    cfg = Config()
    cfg.ai.local.endpoint = "http://localhost:9999"
    cfg.ai.local.model = "custom-model"
    cfg.ai.local.temperature = 0.25
    cfg.ai.local.keep_alive = "45m"
    cfg.ai.local.timeout_ms = 12345

    backend = build_engine(cfg, CATALOGUE).backends["local"]

    assert backend.runtime.endpoint == "http://localhost:9999"
    assert backend.runtime.model == "custom-model"
    assert backend.runtime.keep_alive == "45m"
    assert backend.temperature == 0.25
    assert backend.timeout == pytest.approx(12.345)


def test_specs_from_catalogue_excludes_universal_intents():
    names = {s.name for s in specs_from_catalogue(CATALOGUE)}
    assert "chat" not in names and "unknown" not in names
    assert "answer" not in names
    assert "get_time" in names


def test_general_answer_is_toolless_and_stays_in_app():
    reply = json.dumps({"thought": "explanation", "intent": {
        "type": "answer", "parameters": {}},
        "response_text": "A concise local explanation.", "confidence": 0.9})

    packet = local_backend([reply]).parse("explain recursion", Context(), SPECS)

    assert packet.intent.type == "answer"
    assert packet.needs_skill is False
    assert packet.response_text == "A concise local explanation."
