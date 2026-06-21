"""AST-based calculator: useful maths without eval or arbitrary code."""
from __future__ import annotations

import ast
import math
import operator

from ...ai.schema import Intent, SkillResult
from ..base import Skill

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


class CalculatorSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        expression = str(intent.get("expression", "")).strip()
        if not expression:
            return self.fail("No expression was provided.")
        if len(expression) > 200:
            return self.fail("Expression exceeds the 200-character limit.")
        try:
            tree = ast.parse(expression, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 60:
                raise ValueError("expression is too complex")
            value = self._evaluate(tree.body)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("result is not a finite number")
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError,
                OverflowError) as exc:
            return self.fail(f"Invalid calculation: {exc}",
                             speak="I couldn't evaluate that expression, sir.")
        rendered = f"{value:.12g}"
        return self.ok(f"{expression} = {rendered}",
                       speak=f"The answer is {rendered}, sir.",
                       expression=expression, result=value)

    def _evaluate(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = self._evaluate(node.left), self._evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent exceeds the safe limit")
            return _BINARY[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](self._evaluate(node.operand))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FUNCTIONS and not node.keywords
                and 1 <= len(node.args) <= 2):
            return _FUNCTIONS[node.func.id](
                *(self._evaluate(arg) for arg in node.args))
        raise ValueError("unsupported operation")


SKILL = CalculatorSkill()
