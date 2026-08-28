"""Stage B: what the model decides, and what it is not allowed to touch.

The model decides merge-or-split and writes the wording. Everything mechanical — sources,
due dates, urgency, identifiers — is assembled in code, and these tests exist to prove a
model cannot reach any of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cos.agents.runner import ModelTier
from cos.consolidate.merge import ClusterMerge, consolidate, merge_cluster, render_cluster
from cos.consolidate.prepass import build_clusters
from cos.models import Signal, SourceRef
from cos.settings import ImportantSender, ImportantSenders, UrgencySettings

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
TIER = ModelTier(deployment="gpt-5.5", version="2026-04-24")
URGENCY = UrgencySettings()
SENDERS = ImportantSenders(senders=[ImportantSender(match="Priya Raman", weight=1.0)])
OPERATOR = "Kaan Turgut <kaan@demo.example>"


def ref(kind: str = "mail", id_: str = "m1", author: str = "Priya Raman", h: int = 0) -> SourceRef:
    return SourceRef(
        kind=kind, id=id_, author=author, timestamp=NOW + timedelta(hours=h), excerpt="x"
    )


def sig(statement: str, *sources: SourceRef, **kw: Any) -> Signal:
    return Signal(type=kw.pop("type_", "ask"), statement=statement, sources=list(sources), **kw)


def answer(**kw: Any) -> ClusterMerge:
    base: dict[str, Any] = {
        "merge": True,
        "statement": "Confirm the renewal number",
        "title": "Confirm the renewal number",
        "detail": "",
        "owner": "me",
        "suggested_action": "reply",
        "confidence": 0.9,
        "needs_human_judgment": False,
        "urgency_reason": "explained",
        "split_groups": None,
    }
    base.update(kw)
    return ClusterMerge(**base)


class FakeRunner:
    """Returns scripted answers and records the prompts it was handed."""

    def __init__(self, *answers: ClusterMerge) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def call(self, **kwargs: Any) -> ClusterMerge:
        self.prompts.append(kwargs["prompt"])
        return self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]


async def run(runner: Any, signals: list[Signal]) -> list[Any]:
    return await merge_cluster(
        runner,
        TIER,
        signals,
        now=NOW,
        urgency_settings=URGENCY,
        senders=SENDERS,
        instructions="i",
        operator=OPERATOR,
    )


# ======================================================================================
# what the model cannot touch
# ======================================================================================


async def test_sources_are_the_union_assembled_in_code() -> None:
    """A model asked to copy source lists will eventually drop one."""
    signals = [
        sig("a", ref("mail", "m1")),
        sig("b", ref("chat", "c1", h=1)),
        sig("c", ref("calendar", "e1", h=2)),
    ]
    todo = (await run(FakeRunner(answer()), signals))[0]
    assert {(s.kind, s.id) for s in todo.sources} == {
        ("mail", "m1"),
        ("chat", "c1"),
        ("calendar", "e1"),
    }


async def test_the_same_message_cited_twice_is_carried_once() -> None:
    signals = [sig("a", ref("mail", "m1")), sig("b", ref("mail", "m1"))]
    todo = (await run(FakeRunner(answer()), signals))[0]
    assert len(todo.sources) == 1


async def test_sources_are_time_ordered() -> None:
    signals = [sig("a", ref("mail", "m2", h=5)), sig("b", ref("mail", "m1", h=0))]
    todo = (await run(FakeRunner(answer()), signals))[0]
    assert [s.id for s in todo.sources] == ["m1", "m2"]


async def test_due_is_carried_from_the_signals_not_the_model() -> None:
    """The merge step restating a date is how an inferred deadline gets in sideways."""
    friday = NOW + timedelta(days=2)
    signals = [sig("a", ref(), type_="commitment", due=friday)]
    todo = (await run(FakeRunner(answer()), signals))[0]
    assert todo.due == friday


async def test_the_earliest_explicit_due_wins() -> None:
    signals = [
        sig("a", ref("mail", "m1"), type_="deadline", due=NOW + timedelta(days=3)),
        sig("b", ref("mail", "m2"), type_="deadline", due=NOW + timedelta(days=1)),
    ]
    todo = (await run(FakeRunner(answer()), signals))[0]
    assert todo.due == NOW + timedelta(days=1)


async def test_an_ambiguous_signal_contributes_no_deadline() -> None:
    signals = [sig("look soon-ish", ref("chat", "c1"), ambiguous=True)]
    todo = (await run(FakeRunner(answer()), signals))[0]
    assert todo.due is None


async def test_urgency_is_computed_not_taken_from_the_model() -> None:
    """The schema has no urgency field. This proves the score comes from arithmetic."""
    assert "urgency" not in ClusterMerge.model_fields
    weighted = [sig("a", ref(author="Priya Raman"))]
    unweighted = [sig("a", ref(author="nobody@demo.example"))]
    hot = (await run(FakeRunner(answer()), weighted))[0]
    cold = (await run(FakeRunner(answer()), unweighted))[0]
    assert hot.urgency > cold.urgency


async def test_the_model_cannot_set_sources_due_or_id() -> None:
    for field in ("sources", "due", "id"):
        assert field not in ClusterMerge.model_fields


async def test_identifier_is_content_derived_and_stable() -> None:
    signals = [sig("a", ref("mail", "m1")), sig("b", ref("chat", "c1"))]
    first = (await run(FakeRunner(answer()), signals))[0]
    second = (await run(FakeRunner(answer()), list(reversed(signals))))[0]
    assert first.id == second.id


# ======================================================================================
# merge and split
# ======================================================================================


async def test_a_merge_produces_one_todo() -> None:
    signals = [sig("a", ref("mail", "m1")), sig("b", ref("chat", "c1"))]
    assert len(await run(FakeRunner(answer(merge=True)), signals)) == 1


async def test_a_split_re_asks_each_group_so_it_gets_its_own_wording() -> None:
    """Reusing the cluster's answer gives every piece a reason quoting a score it does
    not have — the exact contradiction the constitution forbids."""
    signals = [sig("approve", ref("mail", "m1")), sig("cancel", ref("mail", "m2"))]
    runner = FakeRunner(
        answer(merge=False, split_groups=[[0], [1]]),
        answer(title="Approve the renewal", urgency_reason="group one"),
        answer(title="Cancel the renewal", urgency_reason="group two"),
    )
    todos = await run(runner, signals)
    assert len(todos) == 2
    assert {t.title for t in todos} == {"Approve the renewal", "Cancel the renewal"}
    assert len(runner.prompts) == 3, "one call for the cluster, one per group"


async def test_split_groups_get_their_own_urgency() -> None:
    """Reusing the cluster's score would inflate every piece of a wrongly grouped set."""
    signals = [
        sig("a", ref("mail", "m1", author="Priya Raman")),
        sig("b", ref("mail", "m2", author="nobody@demo.example")),
    ]
    runner = FakeRunner(answer(merge=False, split_groups=[[0], [1]]), answer(), answer())
    todos = await run(runner, signals)
    assert todos[0].urgency != todos[1].urgency


async def test_a_dropped_index_is_recovered_not_lost() -> None:
    """Losing an ask silently is the one outcome this system exists to prevent."""
    signals = [
        sig("a", ref("mail", "m1")),
        sig("b", ref("mail", "m2")),
        sig("c", ref("mail", "m3")),
    ]
    runner = FakeRunner(answer(merge=False, split_groups=[[0], [1]]), answer(), answer(), answer())
    todos = await run(runner, signals)
    covered = {s.id for t in todos for s in t.sources}
    assert covered == {"m1", "m2", "m3"}


async def test_a_duplicated_index_is_placed_once() -> None:
    signals = [sig("a", ref("mail", "m1")), sig("b", ref("mail", "m2"))]
    runner = FakeRunner(answer(merge=False, split_groups=[[0, 1], [1]]), answer(), answer())
    todos = await run(runner, signals)
    assert sum(len(t.sources) for t in todos) == 2


async def test_a_split_that_yields_one_group_is_treated_as_a_merge() -> None:
    """Otherwise it recurses forever on the same input."""
    signals = [sig("a", ref("mail", "m1")), sig("b", ref("mail", "m2"))]
    runner = FakeRunner(answer(merge=False, split_groups=[[0, 1]]))
    assert len(await run(runner, signals)) == 1


async def test_a_singleton_cluster_is_never_split() -> None:
    signals = [sig("a", ref())]
    assert len(await run(FakeRunner(answer(merge=False, split_groups=[[0]])), signals)) == 1


# ======================================================================================
# the prompt
# ======================================================================================


def test_the_prompt_names_the_operator() -> None:
    """Without it the model reads the operator's own commitments as somebody else's."""
    body = render_cluster([sig("a", ref())], {"score": 10}, operator=OPERATOR)
    assert OPERATOR in body
    assert "owner=me" in body


def test_the_prompt_carries_every_source_excerpt() -> None:
    signals = [sig("a", ref("mail", "m1")), sig("b", ref("chat", "c1"))]
    body = render_cluster(signals, {"score": 10}, operator=OPERATOR)
    assert body.count("[0]") == 1 and body.count("[1]") == 1
    assert "mail" in body and "chat" in body


def test_the_prompt_shows_the_urgency_it_must_explain() -> None:
    body = render_cluster([sig("a", ref())], {"score": 73}, operator=OPERATOR)
    assert "73" in body
    assert "do not change it" in body


def test_an_ambiguous_signal_is_flagged_to_the_model() -> None:
    body = render_cluster([sig("a", ref(), ambiguous=True)], {"score": 1}, operator=OPERATOR)
    assert "ambiguous" in body


# ======================================================================================
# end to end over Stage A
# ======================================================================================


async def test_consolidate_runs_every_cluster_and_ranks_by_urgency() -> None:
    signals = [
        sig("PROJ-1 a", ref("mail", "m1", author="Priya Raman")),
        sig("PROJ-1 b", ref("chat", "c1", author="Priya Raman")),
        sig("unrelated", ref("mail", "m9", author="nobody@demo.example")),
    ]
    clusters = build_clusters(signals)
    runner = FakeRunner(answer())
    todos = await consolidate(
        runner,
        TIER,
        signals,
        clusters,
        now=NOW,
        urgency_settings=URGENCY,
        senders=SENDERS,
        instructions="i",
        operator=OPERATOR,
    )
    assert len(todos) == 2
    assert todos[0].urgency >= todos[1].urgency
    assert len(runner.prompts) == 2, "one call per cluster"


async def test_consolidate_with_nothing_makes_no_calls() -> None:
    runner = FakeRunner(answer())
    todos = await consolidate(
        runner,
        TIER,
        [],
        [],
        now=NOW,
        urgency_settings=URGENCY,
        senders=SENDERS,
        instructions="i",
        operator=OPERATOR,
    )
    assert todos == []
    assert runner.prompts == []


@pytest.mark.parametrize("bad_owner", ["operator", "", "THEM"])
async def test_an_unknown_owner_falls_back_rather_than_failing_the_run(bad_owner: str) -> None:
    """The enum is closed; a model outside it should not kill an otherwise good run."""
    todo = (await run(FakeRunner(answer(owner=bad_owner)), [sig("a", ref())]))[0]
    assert todo.owner == "me"


async def test_human_judgment_forces_no_action() -> None:
    todo = (
        await run(
            FakeRunner(answer(needs_human_judgment=True, suggested_action="reply")),
            [sig("a", ref())],
        )
    )[0]
    assert todo.suggested_action == "no_action"
