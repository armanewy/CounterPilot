from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any, Callable


class FormulaSyntaxError(ValueError):
    pass


class FormulaEvaluationError(ValueError):
    pass


MAX_EXPRESSION_LENGTH = 256
MAX_AST_NODES = 64
MAX_TERMS = 16
MAX_ABS_CONSTANT = 1_000_000.0


def _threshold(x: float, c: float) -> float:
    return 1.0 if x > c else 0.0


def _indicator(value: Any) -> float:
    return 1.0 if bool(value) else 0.0


def _interaction(x: float, y: float) -> float:
    return float(x) * float(y)


ALLOWED_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": lambda x: float(abs(x)),
    "exp": lambda x: float(math.exp(max(min(float(x), 50.0), -50.0))),
    "indicator": _indicator,
    "interaction": _interaction,
    "log": lambda x: float(math.log(max(float(x), 1e-12))),
    "max": lambda *args: float(max(args)),
    "min": lambda *args: float(min(args)),
    "sqrt": lambda x: float(math.sqrt(max(float(x), 0.0))),
    "threshold": _threshold,
}

FUNCTION_ARITY: dict[str, tuple[int, int | None]] = {
    "abs": (1, 1),
    "exp": (1, 1),
    "indicator": (1, 1),
    "interaction": (2, 2),
    "log": (1, 1),
    "max": (1, None),
    "min": (1, None),
    "sqrt": (1, 1),
    "threshold": (2, 2),
}


@dataclass(frozen=True)
class FormulaTerm:
    expression: str
    tree: ast.Expression
    variables: set[str]
    operators: int

    @classmethod
    def parse(cls, expression: str) -> "FormulaTerm":
        expression = str(expression).strip()
        if not expression:
            raise FormulaSyntaxError("Formula terms may not be empty")
        if len(expression) > MAX_EXPRESSION_LENGTH:
            raise FormulaSyntaxError(f"Formula term exceeds {MAX_EXPRESSION_LENGTH} characters")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise FormulaSyntaxError(f"Invalid formula term: {expression}") from exc
        if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
            raise FormulaSyntaxError(f"Formula term exceeds {MAX_AST_NODES} syntax nodes")
        visitor = _Validator()
        visitor.visit(tree)
        return cls(expression=expression, tree=tree, variables=visitor.variables, operators=visitor.operators)

    def evaluate(self, context: dict[str, Any]) -> float:
        try:
            value = float(_eval_node(self.tree.body, context))
        except FormulaSyntaxError:
            raise
        except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
            raise FormulaEvaluationError(f"Could not evaluate formula term {self.expression!r}: {exc}") from exc
        if not math.isfinite(value):
            raise FormulaEvaluationError(f"Formula term {self.expression!r} produced a non-finite value")
        return value


class _Validator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.variables: set[str] = set()
        self.operators = 0

    def generic_visit(self, node: ast.AST) -> None:
        allowed = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.Call,
            ast.Compare,
            ast.BoolOp,
            ast.And,
            ast.Or,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Pow,
            ast.USub,
            ast.UAdd,
            ast.Gt,
            ast.GtE,
            ast.Lt,
            ast.LtE,
            ast.Eq,
            ast.NotEq,
        )
        if not isinstance(node, allowed):
            raise FormulaSyntaxError(f"Unsupported syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, bool):
            return
        if not isinstance(node.value, (int, float)):
            raise FormulaSyntaxError("Only numeric and boolean constants are allowed")
        value = float(node.value)
        if not math.isfinite(value) or abs(value) > MAX_ABS_CONSTANT:
            raise FormulaSyntaxError("Formula constant is non-finite or unreasonably large")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise FormulaSyntaxError("Dunder names are forbidden")
        if node.id not in ALLOWED_FUNCTIONS:
            self.variables.add(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        if node.keywords:
            raise FormulaSyntaxError("Formula functions do not accept keyword arguments")
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
            raise FormulaSyntaxError("Only whitelisted formula functions are allowed")
        minimum, maximum = FUNCTION_ARITY[node.func.id]
        count = len(node.args)
        if count < minimum or (maximum is not None and count > maximum):
            expected = str(minimum) if minimum == maximum else f"at least {minimum}"
            raise FormulaSyntaxError(
                f"Function {node.func.id!r} expects {expected} argument(s), received {count}"
            )
        self.operators += 1
        for arg in node.args:
            self.visit(arg)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        self.operators += 1
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        self.operators += 1
        self.visit(node.operand)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.operators += len(node.ops)
        self.visit(node.left)
        for comparator in node.comparators:
            self.visit(comparator)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.operators += max(0, len(node.values) - 1)
        for value in node.values:
            self.visit(value)


def _eval_node(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ALLOWED_FUNCTIONS:
            return ALLOWED_FUNCTIONS[node.id]
        value = context.get(node.id, 0.0)
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if not isinstance(value, (int, float)):
            raise FormulaEvaluationError(f"Variable {node.id!r} is not numeric")
        return float(value)
    if isinstance(node, ast.BinOp):
        left = float(_eval_node(node.left, context))
        right = float(_eval_node(node.right, context))
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if abs(right) > 1e-12 else 0.0
        if isinstance(node.op, ast.Pow):
            exponent = max(min(right, 6.0), -6.0)
            if left < 0.0 and not float(exponent).is_integer():
                raise FormulaEvaluationError("Fractional powers of negative values are undefined")
            return float(math.pow(left, exponent))
    if isinstance(node, ast.UnaryOp):
        value = float(_eval_node(node.operand, context))
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval_node(comparator, context)
            if isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            else:
                ok = False
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [bool(_eval_node(value, context)) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = ALLOWED_FUNCTIONS[node.func.id]
        args = [_eval_node(arg, context) for arg in node.args]
        return fn(*args)
    raise FormulaSyntaxError(f"Cannot evaluate syntax: {type(node).__name__}")


@dataclass(frozen=True)
class Formula:
    terms: list[FormulaTerm]

    @classmethod
    def parse(cls, terms: list[str]) -> "Formula":
        if len(terms) > MAX_TERMS:
            raise FormulaSyntaxError(f"A formula may contain at most {MAX_TERMS} terms")
        cleaned = [str(term).strip() for term in terms]
        if len(set(cleaned)) != len(cleaned):
            raise FormulaSyntaxError("Duplicate formula terms are not allowed")
        return cls([FormulaTerm.parse(term) for term in cleaned])

    @property
    def variables(self) -> set[str]:
        names: set[str] = set()
        for term in self.terms:
            names.update(term.variables)
        return names

    @property
    def operator_count(self) -> int:
        return sum(term.operators for term in self.terms)

    @property
    def complexity(self) -> int:
        return len(self.terms) + len(self.variables) + self.operator_count

    def vector(self, context: dict[str, Any]) -> list[float]:
        return [1.0] + [term.evaluate(context) for term in self.terms]
