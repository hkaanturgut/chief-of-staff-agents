"""Constitution Principle III, enforced mechanically.

Clustering and ranking must contain no model call. Stated as a principle it is a thing
people agree with and then quietly violate under deadline; stated as an import-graph
assertion it is a failing test.

The check is transitive, because `prepass` importing a helper that imports the agent
runner is the same violation wearing a hat.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil

import pytest

DETERMINISTIC_MODULES = [
    "cos.consolidate.prepass",
    "cos.consolidate.entities",
    "cos.consolidate.rank",
]

FORBIDDEN_PREFIXES = (
    "cos.agents",
    "agent_framework",
    "openai",
    "azure.ai",
)


def _transitive_cos_imports(module_name: str) -> set[str]:
    """Every module reachable from `module_name`, following cos.* edges."""
    seen: set[str] = set()
    stack = [module_name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        module = importlib.import_module(current)
        for value in vars(module).values():
            name = getattr(value, "__module__", None) or getattr(value, "__name__", None)
            if isinstance(name, str) and name.startswith("cos.") and name not in seen:
                stack.append(name)
    return seen


@pytest.mark.parametrize("module_name", DETERMINISTIC_MODULES)
def test_deterministic_modules_import_no_model_machinery(module_name: str) -> None:
    if importlib.util.find_spec(module_name) is None:
        pytest.skip(f"{module_name} not built yet (Phase 3)")

    reachable = _transitive_cos_imports(module_name)
    for name in reachable:
        module = importlib.import_module(name)
        for attr in vars(module).values():
            origin = getattr(attr, "__module__", "") or ""
            assert not origin.startswith(FORBIDDEN_PREFIXES), (
                f"{name} pulls in {origin}; the deterministic pre-pass must contain "
                "no model call (Constitution III)"
            )


def test_the_deterministic_modules_are_expected_to_exist_eventually() -> None:
    """Fails once Phase 3 lands and someone deletes a module rather than fixing it."""
    package = importlib.import_module("cos.consolidate")
    built = {f"cos.consolidate.{m.name}" for m in pkgutil.iter_modules(package.__path__)}
    missing = [m for m in DETERMINISTIC_MODULES if m not in built]
    if missing:
        pytest.skip(f"Phase 3 not complete; still to build: {', '.join(missing)}")
