"""Safe calculator routing and evaluation tests."""
from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine
from axon.ai.schema import Intent
from axon.skills.registry import SkillRegistry


def registry():
    return SkillRegistry().discover()


def test_spoken_arithmetic_uses_fast_local_expression():
    r = registry()
    engine = LocalIntentEngine(r.catalogue())

    packet = engine.interpret("calculate 17 times 6", Context())
    result = r.execute(packet.intent)

    assert packet.intent.type == "calculate"
    assert packet.intent.get("expression") == "17 * 6"
    assert result.ok is True
    assert result.data["result"] == 102


def test_percentage_expression_is_normalized():
    r = registry()
    packet = LocalIntentEngine(r.catalogue()).interpret(
        "what is 20 percent of 50", Context())

    result = r.execute(packet.intent)

    assert result.ok is True
    assert result.data["result"] == 10


def test_calculator_supports_functions_and_constants():
    result = registry().execute(Intent(
        type="calculate", parameters={"expression": "sqrt(81) + pi"}))

    assert result.ok is True
    assert 12.14 < result.data["result"] < 12.15


def test_calculator_rejects_code_and_unbounded_exponents():
    r = registry()
    code = r.execute(Intent(type="calculate", parameters={
        "expression": "__import__('os').system('whoami')"}))
    exponent = r.execute(Intent(type="calculate", parameters={
        "expression": "2 ** 1000"}))

    assert code.ok is False
    assert exponent.ok is False
