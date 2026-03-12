from __future__ import annotations

import ast
import math
import operator

from lazyclaw.skills.base import BaseSkill


class CalculateSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "calculate"

    @property
    def description(self) -> str:
        return "Evaluate a mathematical expression safely. Supports +, -, *, /, **, %, and common math functions."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate (e.g., '15 * 847 / 100', 'sqrt(144)')",
                },
            },
            "required": ["expression"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        expr = params["expression"]
        try:
            result = _safe_eval(expr)
            return f"{expr} = {result}"
        except Exception as e:
            return f"Cannot evaluate '{expr}': {e}"


# Safe math evaluator using AST
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_FUNCTIONS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval(expr: str):
    """Safely evaluate a math expression using AST parsing."""
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value}")
    elif isinstance(node, ast.BinOp):
        op = _OPERATORS.get(type(node.op))
        if not op:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    elif isinstance(node, ast.UnaryOp):
        op = _OPERATORS.get(type(node.op))
        if not op:
            raise ValueError("Unsupported unary operator")
        return op(_eval_node(node.operand))
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
            func = _FUNCTIONS[node.func.id]
            if callable(func):
                args = [_eval_node(arg) for arg in node.args]
                return func(*args)
            return func  # constants like pi, e
        raise ValueError(f"Unknown function: {getattr(node.func, 'id', '?')}")
    elif isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            val = _FUNCTIONS[node.id]
            if not callable(val):
                return val  # pi, e
        raise ValueError(f"Unknown variable: {node.id}")
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")
