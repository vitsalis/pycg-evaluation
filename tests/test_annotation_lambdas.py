"""The compiler facts behind the lambda inventory of
evaluation/rq1-accuracy/trace_recall.py: which annotation forms create a
lambda in a scope. They run on every supported Python (the inventory's own
test, in test_trace_recall.py, needs Python 3.14), so the forms that
Python 3.14 defers to `__annotate__` are checked against the compiler on
3.12 and 3.13, which create them where Python 3.10, the runtime that
DyPyBench traced, does."""

import sys
import types

import pytest


def code_lambdas(code, scope):
    """The lambdas that the compiler puts directly in the code object named
    `scope` ("<module>" for the module), or None if there is no such code."""
    if code.co_name == scope:
        return sum(
            1
            for const in code.co_consts
            if isinstance(const, types.CodeType) and const.co_name == "<lambda>"
        )
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            found = code_lambdas(const, scope)
            if found is not None:
                return found
    return None


# (source, scope, lambdas, same on every Python): the lambdas that the
# compiler of the traced runtime (Python 3.10, DyPyBench's) creates in that
# scope. An annotation that Python 3.14 defers to `__annotate__` is only
# checked against the compiler before 3.14.
ANNOTATIONS = [
    ("def factory():\n    x: (lambda: 0)\n    return lambda: 1\n", "factory", 1, True),
    ("def f(o):\n    o.x: (lambda: 0) = 1\n    return lambda: 1\n", "f", 1, True),
    ("def f(d):\n    d[(lambda: 0)()]: int = 1\n", "f", 1, True),
    ("def f():\n    x: int = (lambda: 0)\n", "f", 1, True),
    ("def f(a=lambda: 0, *, b=lambda: 1):\n    pass\n", "<module>", 2, True),
    (
        "from __future__ import annotations\n"
        "x: (lambda: 0) = 1\n"
        "def f(a: (lambda: 0)) -> (lambda: 1):\n    pass\n",
        "<module>",
        0,
        True,
    ),
    (
        "from __future__ import annotations\nclass C:\n    x: (lambda: 0)\n",
        "C",
        0,
        True,
    ),
    ("x: (lambda: 0) = 1\n", "<module>", 1, False),
    ("o = None\no.x: (lambda: 0)\n", "<module>", 1, False),
    ("class C:\n    x: (lambda: 0)\n", "C", 1, False),
    ("def f(a: (lambda: 0), *b: (lambda: 1)) -> (lambda: 2):\n    pass\n",
     "<module>", 3, False),
    ("def outer():\n    def inner(a: (lambda: 0)):\n        pass\n", "outer", 1, False),
]  # fmt: skip


@pytest.mark.parametrize(("source", "scope", "lambdas", "stable"), ANNOTATIONS)
def test_the_compiler_creates_a_lambda_only_where_an_annotation_runs(
    source, scope, lambdas, stable
):
    if not stable and sys.version_info >= (3, 14):
        pytest.skip("Python 3.14 defers this annotation to __annotate__")
    assert code_lambdas(compile(source, "m.py", "exec"), scope) == lambdas
