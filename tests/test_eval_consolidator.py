"""The scored evaluation.

This is the answer to "how do you test a non-deterministic system", and it is a slide as
much as a test file.

It is NOT an equality assertion. The consolidator is model-backed and its wording will
vary between runs — that is fine and expected. What must not vary is whether it found the
right things: whether the triple collapsed, whether the buried commitment survived,
whether a deadline was invented.

So each trap becomes a metric with a threshold, and the thresholds fail CI.

The evaluation needs live models, so it is marked `live` and excluded from the default
run. `uv run pytest -m live` executes it. CI runs the deterministic suite; the evaluation
runs before a rehearsal, when a model version changes, and whenever an agent's
instructions are edited.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cos.models import TodoItem

EXPECTATIONS = json.loads((Path(__file__).parent / "golden" / "expectations.json").read_text())
THRESHOLDS = EXPECTATIONS["thresholds"]
TRAPS = EXPECTATIONS["traps"]

pytestmark = pytest.mark.live


def source_ids(todo: TodoItem) -> set[str]:
    return {s.id for s in todo.sources}


def find_by_source(todos: list[TodoItem], source_id: str) -> TodoItem | None:
    return next((t for t in todos if source_id in source_ids(t)), None)


# --- metrics ----------------------------------------------------------------------------


def dedup_recall(todos: list[TodoItem]) -> float:
    """Of the sources that belong to one ask, how many ended up on a single to-do."""
    wanted = set(TRAPS["triple"]["must_merge_sources"])
    best = max((len(source_ids(t) & wanted) for t in todos), default=0)
    return best / len(wanted)


def false_merge_rate(todos: list[TodoItem]) -> float:
    """Pairs that must not share a to-do, but do.

    A wrong merge hides an ask completely, which is worse than a wrong split — so this
    threshold is tighter than dedup recall.
    """
    pairs = TRAPS["split"]["sources_that_must_not_merge"]
    wrong = sum(1 for a, b in pairs if any({a, b} <= source_ids(t) for t in todos))
    return wrong / len(pairs) if pairs else 0.0


def buried_commitment_recall(todos: list[TodoItem]) -> float:
    return 1.0 if find_by_source(todos, TRAPS["buried_commitment"]["must_include_source"]) else 0.0


def invented_deadlines(todos: list[TodoItem]) -> int:
    """Must be zero. An invented deadline is worse than a missed one: it gets acted on."""
    trap = TRAPS["invented_deadline"]
    todo = find_by_source(todos, trap["source"])
    if todo is None:
        return 0
    return 1 if todo.due is not None and source_ids(todo) == {trap["source"]} else 0


def score(todos: list[TodoItem]) -> dict[str, Any]:
    return {
        "todos": len(todos),
        "dedup_recall": dedup_recall(todos),
        "false_merge_rate": false_merge_rate(todos),
        "buried_commitment_recall": buried_commitment_recall(todos),
        "invented_deadlines": invented_deadlines(todos),
    }


# --- the run ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def todos() -> list[TodoItem]:
    import asyncio

    from cos.corpus import load
    from cos.pipeline import run_brief
    from cos.settings import load_environment, load_important_senders, load_settings

    bundle, now, operator = load()
    result = asyncio.run(
        run_brief(
            settings=load_settings(),
            env=load_environment(),
            senders=load_important_senders(),
            auth=None,
            now=now,
            bundle_override=bundle,
            operator_override=operator,
        )
    )
    return result.todos


# --- the assertions -------------------------------------------------------------------------


def test_report(todos: list[TodoItem]) -> None:
    """Always prints the scorecard, so a failure elsewhere is readable in context."""
    report = score(todos)
    print("\n  consolidator evaluation")
    for key, value in report.items():
        threshold = THRESHOLDS.get(key)
        marker = "" if threshold is None else f"  (threshold {threshold})"
        print(f"    {key:28} {value}{marker}")


def test_dedup_recall_meets_threshold(todos: list[TodoItem]) -> None:
    """Did the triple collapse. This is the money moment, as a number."""
    value = dedup_recall(todos)
    assert value >= THRESHOLDS["dedup_recall"], (
        f"dedup recall {value:.2f} below {THRESHOLDS['dedup_recall']}: the same ask "
        "arriving through several channels did not become one to-do"
    )


def test_false_merge_rate_within_threshold(todos: list[TodoItem]) -> None:
    value = false_merge_rate(todos)
    assert value <= THRESHOLDS["false_merge_rate"], (
        f"false merge rate {value:.2f} above {THRESHOLDS['false_merge_rate']}: two "
        "distinct asks were merged, which hides one of them entirely"
    )


def test_the_buried_commitment_survives(todos: list[TodoItem]) -> None:
    assert buried_commitment_recall(todos) >= THRESHOLDS["buried_commitment_recall"], (
        "the commitment buried in the budget thread was lost"
    )


def test_no_deadline_was_invented(todos: list[TodoItem]) -> None:
    """Threshold zero. Not a target — a requirement."""
    assert invented_deadlines(todos) <= THRESHOLDS["invented_deadlines"], (
        "a deadline was invented from tone. 'soon-ish' is not a date."
    )


def test_the_polite_trap_ranks_below_real_work(todos: list[TodoItem]) -> None:
    trap = TRAPS["polite_trap"]
    loud = find_by_source(todos, trap["source"])
    if loud is None:
        return  # correctly dismissed as needing nothing
    for source in trap["must_rank_below_sources"]:
        real = find_by_source(todos, source)
        if real is not None:
            assert loud.urgency < real.urgency, (
                f"the newsletter outranked {source}; urgency is being read from tone"
            )


def test_the_high_risk_item_outranks_noise(todos: list[TodoItem]) -> None:
    trap = TRAPS["high_risk"]
    invoice = find_by_source(todos, trap["source"])
    assert invoice is not None, "the overdue invoice was dropped entirely"
    for source in trap["must_outrank_sources"]:
        noise = find_by_source(todos, source)
        if noise is not None:
            assert invoice.urgency > noise.urgency


def test_the_brief_is_not_mostly_noise(todos: list[TodoItem]) -> None:
    """A to-do list padded with things nobody has to do is worse than a shorter one."""
    inert = [t for t in todos if t.suggested_action == "no_action"]
    assert len(inert) <= THRESHOLDS["noise_todos_max"], (
        f"{len(inert)} to-dos propose no action; the brief is becoming an inbox summary"
    )
