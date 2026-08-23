"""Urgency arithmetic.

Urgency is computed, not vibed. These are equality assertions because there is no model
in the loop — which is the whole argument for computing it in code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cos.consolidate.rank import (
    compute_urgency,
    due_component,
    earliest_explicit_due,
    explain_inputs,
    sender_component,
    sources_component,
)
from cos.models import Signal, SourceRef
from cos.settings import ImportantSender, ImportantSenders, UrgencySettings

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
SETTINGS = UrgencySettings()
NOBODY = ImportantSenders()
PRIYA = ImportantSenders(
    senders=[
        ImportantSender(match="priya@demo.example", weight=1.0),
        ImportantSender(match="@board.demo.example", weight=0.9),
    ]
)


def ref(kind: str = "mail", id_: str = "m1", author: str = "someone@demo.example") -> SourceRef:
    return SourceRef(kind=kind, id=id_, author=author, timestamp=NOW)


def urgency(**kw: object) -> int:
    params: dict = {
        "due": None,
        "sources": [ref()],
        "now": NOW,
        "settings": SETTINGS,
        "senders": NOBODY,
    }
    params.update(kw)
    return compute_urgency(**params)


# --- due proximity ---------------------------------------------------------------------


def test_no_due_date_scores_nothing() -> None:
    assert due_component(None, NOW, SETTINGS) == 0.0


def test_imminent_deadline_scores_full() -> None:
    assert due_component(NOW + timedelta(hours=6), NOW, SETTINGS) == 1.0


def test_overdue_scores_full_and_does_not_overflow() -> None:
    """Past due is maximally urgent. There is nothing more urgent than that."""
    assert due_component(NOW - timedelta(days=5), NOW, SETTINGS) == 1.0


def test_distant_deadline_scores_nothing() -> None:
    assert due_component(NOW + timedelta(days=30), NOW, SETTINGS) == 0.0


def test_due_component_decays_monotonically() -> None:
    scores = [due_component(NOW + timedelta(hours=h), NOW, SETTINGS) for h in (24, 48, 96, 168)]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


# --- sender importance -----------------------------------------------------------------


def test_unknown_sender_contributes_nothing() -> None:
    assert sender_component([ref(author="stranger@demo.example")], PRIYA) == 0.0


def test_configured_sender_contributes_its_weight() -> None:
    assert sender_component([ref(author="priya@demo.example")], PRIYA) == 1.0


def test_sender_matching_is_case_insensitive() -> None:
    assert sender_component([ref(author="PRIYA@DEMO.EXAMPLE")], PRIYA) == 1.0


def test_domain_entries_match_by_suffix() -> None:
    assert sender_component([ref(author="chair@board.demo.example")], PRIYA) == 0.9


def test_highest_weight_among_sources_wins() -> None:
    sources = [
        ref(id_="m1", author="stranger@demo.example"),
        ref(id_="m2", author="priya@demo.example"),
    ]
    assert sender_component(sources, PRIYA) == 1.0


# --- distinct sources ------------------------------------------------------------------


def test_one_source_scores_a_third() -> None:
    assert sources_component([ref()], SETTINGS) == pytest.approx(1 / 3)


def test_three_sources_saturate() -> None:
    sources = [
        ref(kind="mail", id_="m1"),
        ref(kind="chat", id_="c1"),
        ref(kind="calendar", id_="e1"),
    ]
    assert sources_component(sources, SETTINGS) == 1.0


def test_the_same_message_cited_twice_counts_once() -> None:
    """Otherwise an agent citing one email twice would inflate its own urgency."""
    assert sources_component([ref(id_="m1"), ref(id_="m1")], SETTINGS) == pytest.approx(1 / 3)


def test_more_sources_than_saturation_does_not_exceed_one() -> None:
    sources = [ref(id_=f"m{i}") for i in range(10)]
    assert sources_component(sources, SETTINGS) == 1.0


# --- the composed score ----------------------------------------------------------------


def test_bounds_hold() -> None:
    assert urgency() >= 0
    everything = urgency(
        due=NOW + timedelta(hours=1),
        sources=[
            ref(kind="mail", id_="m1", author="priya@demo.example"),
            ref(kind="chat", id_="c1"),
            ref(kind="calendar", id_="e1"),
        ],
        senders=PRIYA,
    )
    assert everything == 100


def test_the_score_is_stable_across_runs() -> None:
    assert urgency(due=NOW + timedelta(hours=30)) == urgency(due=NOW + timedelta(hours=30))


def test_trap_4_the_polite_trap_ranks_below_everything_real() -> None:
    """An email that reads urgent but asks for nothing.

    No deadline, no weighted sender, one source. There is nothing for it to score on,
    however many exclamation marks it contains — which is exactly why urgency is
    computed rather than inferred from tone.
    """
    polite_trap = urgency(sources=[ref(id_="m-polite", author="stranger@demo.example")])

    has_deadline = urgency(due=NOW + timedelta(hours=12), sources=[ref(id_="m-due")])
    important_sender = urgency(
        sources=[ref(id_="m-vip", author="priya@demo.example")], senders=PRIYA
    )
    the_triple = urgency(
        sources=[
            ref(kind="mail", id_="m1"),
            ref(kind="chat", id_="c1"),
            ref(kind="calendar", id_="e1"),
        ]
    )

    assert polite_trap < has_deadline
    assert polite_trap < important_sender
    assert polite_trap < the_triple


def test_the_triple_outranks_a_single_mention() -> None:
    single = urgency(sources=[ref(id_="m1")])
    triple = urgency(
        sources=[
            ref(kind="mail", id_="m1"),
            ref(kind="chat", id_="c1"),
            ref(kind="calendar", id_="e1"),
        ]
    )
    assert triple > single


# --- the explanation handed to the model ------------------------------------------------


def test_explain_inputs_matches_the_computed_score() -> None:
    """The model explains the real number. A reason that contradicts the arithmetic is
    worse than no reason at all."""
    sources = [ref(id_="m1", author="priya@demo.example")]
    due = NOW + timedelta(hours=10)
    breakdown = explain_inputs(due=due, sources=sources, now=NOW, settings=SETTINGS, senders=PRIYA)
    assert breakdown["score"] == compute_urgency(
        due=due, sources=sources, now=NOW, settings=SETTINGS, senders=PRIYA
    )
    assert breakdown["due_component"] == 1.0
    assert breakdown["distinct_sources"] == 1


# --- carrying the deadline forward -------------------------------------------------------


def test_earliest_explicit_due_is_carried_forward() -> None:
    """Taken from the signals in code. The merge model never restates a date."""
    signals = [
        Signal(type="ask", statement="a", sources=[ref(id_="m1")]),
        Signal(
            type="deadline", statement="b", due=NOW + timedelta(days=3), sources=[ref(id_="m2")]
        ),
        Signal(
            type="deadline", statement="c", due=NOW + timedelta(days=1), sources=[ref(id_="m3")]
        ),
    ]
    assert earliest_explicit_due(signals) == NOW + timedelta(days=1)


def test_no_explicit_due_stays_none() -> None:
    signals = [Signal(type="ask", statement="a", sources=[ref()])]
    assert earliest_explicit_due(signals) is None
