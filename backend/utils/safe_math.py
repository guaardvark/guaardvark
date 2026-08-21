"""Arithmetic-only expression evaluation for chat/search "calculate" intents."""

from __future__ import annotations

import ast
import operator
from typing import Union

Number = Union[int, float]

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

MAX_NODES = 200
MAX_ABS_OPERAND = 1e15
MAX_ABS_EXPONENT = 64
MAX_ABS_RESULT = 1e100


def evaluate_arithmetic(expression: str) -> Number:
    """Evaluate ``expression`` using only numbers, + - * / // % ** and parentheses.

    Raises ``ValueError`` for anything else (names, calls, attributes, strings),
    for expressions large enough to stall the process (deep trees, huge
    operands, large exponents), and ``ZeroDivisionError`` where Python would.
    """
    if not expression or not expression.strip():
        raise ValueError("empty expression")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid arithmetic: {exc.msg}") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ValueError("expression too long")
    return _eval(tree.body)


def _check(value: Number) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("non-numeric value")
    if abs(value) > MAX_ABS_RESULT:
        raise ValueError("result too large")
    return value


def _eval(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        if abs(node.value) > MAX_ABS_OPERAND:
            raise ValueError("operand too large")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _check(_UNARY[type(node.op)](_eval(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_ABS_EXPONENT:
            raise ValueError("exponent too large")
        return _check(_BINARY[type(node.op)](left, right))
    raise ValueError(f"unsupported expression element: {type(node).__name__}")
