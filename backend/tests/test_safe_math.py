"""evaluate_arithmetic: the calculator behind the search 'calculate' intent."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.utils.safe_math import evaluate_arithmetic  # noqa: E402


@pytest.mark.parametrize("expr,expected", [
    ("2+2", 4),
    ("(3 + 4) * 2", 14),
    ("10 / 4", 2.5),
    ("7 // 2", 3),
    ("7 % 3", 1),
    ("2 ** 10", 1024),
    ("-3 + 5", 2),
    ("1.5 * 4", 6.0),
])
def test_arithmetic(expr, expected):
    assert evaluate_arithmetic(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",
    "().__class__",
    "abs(1)",
    "x + 1",
    "'a' * 3",
    "",
    "   ",
    "2 +",
    "9 ** 9 ** 9",
    "10 ** 65",
    "1e16 + 1",
    "1 < 2",
])
def test_rejects_non_arithmetic_and_runaway(expr):
    with pytest.raises(ValueError):
        evaluate_arithmetic(expr)


def test_division_by_zero_propagates():
    with pytest.raises(ZeroDivisionError):
        evaluate_arithmetic("1 / 0")


def test_node_limit():
    with pytest.raises(ValueError):
        evaluate_arithmetic("+".join(["1"] * 300))
