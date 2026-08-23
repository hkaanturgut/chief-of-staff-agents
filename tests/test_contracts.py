"""The invariants from data-model.md, each as a test that construction fails.

These are the constitution's principles expressed as code. If one of these stops
failing, a principle has quietly become optional.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from cos.models import (
    CalendarEvent,
    ChatTarget,
    MailTarget,
    ProposedAction,
    Signal,
    SourceRef,
    TodoItem,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
ID26 = "01JX8ZQ4K7N2M5P8R3T6V9W1XY"


def ref(kind: str = "mail", id_: str = "AAMk-1") -> SourceRef:
    return SourceRef(kind=kind, id=id_, author="Priya", timestamp=NOW, excerpt="hello")


# --- Principle IV: provenance or it does not exist ------------------------------------


def test_signal_requires_at_least_one_source() -> None:
    with pytest.raises(ValidationError):
        Signal(type="ask", statement="do the thing", sources=[])


def test_todo_requires_at_least_one_source() -> None:
    with pytest.raises(ValidationError):
        TodoItem(
            id=ID26,
            title="t",
            owner="me",
            urgency=0,
            suggested_action="reply",
            confidence=0.5,
            sources=[],
        )


def test_action_requires_at_least_one_source() -> None:
    with pytest.raises(ValidationError):
        ProposedAction(
            id=ID26,
            todo_id=ID26,
            kind="send_mail",
            risk="low",
            target=MailTarget(to=["a@b.example"]),
            rationale="because",
            sources=[],
        )


# --- Principle IV: a deadline is never inferred ---------------------------------------


def test_ambiguous_signal_cannot_carry_a_due_date() -> None:
    """The 'soon-ish' trap. Uncertain and dated is a contradiction, not a judgement call."""
    with pytest.raises(ValidationError, match="ambiguous"):
        Signal(
            type="ask",
            statement="look at this soon-ish",
            ambiguous=True,
            due=NOW + timedelta(days=3),
            sources=[ref()],
        )


def test_ambiguous_signal_without_a_due_date_is_fine() -> None:
    s = Signal(type="ask", statement="look at this soon-ish", ambiguous=True, sources=[ref()])
    assert s.due is None


def test_explicit_due_date_on_an_unambiguous_signal_is_fine() -> None:
    s = Signal(
        type="commitment",
        statement="send the revised numbers by Friday",
        due=NOW + timedelta(days=3),
        sources=[ref()],
    )
    assert s.due is not None


# --- FR-018: no draft for what the system does not understand -------------------------


def test_human_judgment_forces_no_action() -> None:
    with pytest.raises(ValidationError, match="needs_human_judgment"):
        TodoItem(
            id=ID26,
            title="unclear ask",
            owner="me",
            urgency=10,
            suggested_action="reply",
            confidence=0.2,
            needs_human_judgment=True,
            sources=[ref()],
        )


def test_human_judgment_with_no_action_is_fine() -> None:
    t = TodoItem(
        id=ID26,
        title="unclear ask",
        owner="me",
        urgency=10,
        suggested_action="no_action",
        confidence=0.2,
        needs_human_judgment=True,
        sources=[ref()],
    )
    assert t.suggested_action == "no_action"


# --- Principle V: forbid extras, never coerce -----------------------------------------


def test_extra_fields_are_rejected() -> None:
    """An agent inventing a field fails the run rather than having it silently dropped."""
    with pytest.raises(ValidationError):
        Signal(
            type="ask",
            statement="s",
            sources=[ref()],
            priority="high",  # type: ignore[call-arg]
        )


def test_models_are_frozen() -> None:
    s = Signal(type="ask", statement="s", sources=[ref()])
    with pytest.raises(ValidationError):
        s.statement = "something else"  # type: ignore[misc]


# --- timezone awareness ----------------------------------------------------------------


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        SourceRef(kind="mail", id="x", author="a", timestamp=datetime(2026, 8, 22, 12, 0))


def test_naive_due_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Signal(type="deadline", statement="s", due=datetime(2026, 8, 25, 9, 0), sources=[ref()])


# --- bounds ----------------------------------------------------------------------------


def test_urgency_is_bounded() -> None:
    for bad in (-1, 101):
        with pytest.raises(ValidationError):
            TodoItem(
                id=ID26,
                title="t",
                owner="me",
                urgency=bad,
                suggested_action="reply",
                confidence=0.5,
                sources=[ref()],
            )


def test_excerpt_is_capped_for_the_pull_request_body() -> None:
    with pytest.raises(ValidationError):
        SourceRef(kind="mail", id="x", author="a", timestamp=NOW, excerpt="x" * 241)


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        TodoItem(
            id=ID26,
            title="t",
            owner="me",
            urgency=0,
            suggested_action="reply",
            confidence=1.5,
            sources=[ref()],
        )


# --- targets are typed, not a bare dict ------------------------------------------------


def test_target_must_match_kind() -> None:
    """The executor branches on kind. A mismatched target is where a wrong send hides."""
    with pytest.raises(ValidationError, match="requires a MailTarget"):
        ProposedAction(
            id=ID26,
            todo_id=ID26,
            kind="send_mail",
            risk="low",
            target=ChatTarget(chat_id="19:abc"),
            rationale="r",
            sources=[ref()],
        )


def test_mail_target_needs_a_recipient() -> None:
    with pytest.raises(ValidationError):
        MailTarget(to=[])


# --- source identity -------------------------------------------------------------------


def test_source_refs_dedupe_on_kind_and_id() -> None:
    """Merging two signals that cite the same message must not double-cite it."""
    a = SourceRef(kind="mail", id="X", author="Priya", timestamp=NOW, excerpt="one")
    b = SourceRef(kind="mail", id="X", author="Priya", timestamp=NOW, excerpt="two")
    assert a == b
    assert len({a, b}) == 1


def test_source_refs_of_different_kinds_are_distinct() -> None:
    a = SourceRef(kind="mail", id="X", author="P", timestamp=NOW)
    b = SourceRef(kind="chat", id="X", author="P", timestamp=NOW)
    assert a != b


# --- misc ------------------------------------------------------------------------------


def test_event_end_cannot_precede_start() -> None:
    with pytest.raises(ValidationError, match="precedes"):
        CalendarEvent(id="e1", start=NOW, end=NOW - timedelta(hours=1), organizer="a@b.example")
