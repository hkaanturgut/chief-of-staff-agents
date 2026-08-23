"""Constitution I, enforced mechanically.

No module outside the executor may reach a provider write. Stated as a principle this is
something everyone agrees with and then quietly violates under deadline; stated as an
import-graph assertion it is a failing test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[1] / "src" / "cos"

# The only module permitted to call a Graph or GitHub write.
SENDER = "outbox/executor.py"

WRITE_METHODS = {"post", "request"}
WRITE_PATHS = ("sendMail", "/send", "/me/events", "/messages", "createReply")


def python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_the_executor_calls_graph_write_methods() -> None:
    offenders: list[str] = []
    for path in python_files():
        relative = str(path.relative_to(SRC))
        if relative in {SENDER, "graph/client.py", "sources/mail.py", "sources/calendar.py"}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if any(marker in arg.value for marker in WRITE_PATHS):
                            offenders.append(f"{relative}: {arg.value}")
    assert not offenders, (
        "only outbox/executor.py may perform provider writes (Constitution I); found: "
        + ", ".join(offenders)
    )


def imported_modules(path: Path) -> set[str]:
    """Modules a file imports. Comments and docstrings are not imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("module", ["draft/drafter.py", "pipeline.py", "brief.py"])
def test_proposing_code_cannot_reach_the_executor(module: str) -> None:
    """The drafter proposes. It must have no path to performing what it proposed."""
    imports = imported_modules(SRC / module)
    assert not any("executor" in name for name in imports), (
        f"{module} imports the executor; only the execute path may send"
    )


def test_the_allowlist_is_checked_before_the_ledger() -> None:
    """Order matters: a forbidden recipient must fail even if somehow already recorded."""
    assert _line_of("execute", "check") < _line_of("execute", "check_and_reserve")


def _line_of(function_name: str, attribute: str) -> int:
    """First line inside a named function where an attribute is touched.

    Parsed rather than grepped, so a docstring describing the ordering cannot satisfy or
    break the assertion.
    """
    tree = ast.parse((SRC / "outbox" / "executor.py").read_text())
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == attribute
    )


def test_dry_run_is_checked_before_performing() -> None:
    assert _line_of("execute", "dry_run") < _line_of("execute", "_perform")


def test_the_ledger_is_checked_before_performing() -> None:
    assert _line_of("execute", "check_and_reserve") < _line_of("execute", "_perform")
