"""Proposal files round-trip, and a human edit survives.

The file is the review surface. If writing then reading changes anything, the diff a
reviewer approved is not the thing that gets sent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cos.models import IssueTarget, MailTarget, ProposedAction, SourceRef
from cos.outbox import writer
from cos.outbox.reader import ProposalError, parse

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def action(**kw: object) -> ProposedAction:
    base: dict = {
        "id": "01JX8ZQ4K7N2M5P8R3T6V9W1XY",
        "todo_id": "01JX7YP3J6M1L4N7Q2S5U8V0WX",
        "kind": "reply_mail",
        "risk": "medium",
        "target": MailTarget(to=["priya@demo.example"], subject="Re: renewal", in_reply_to="m1"),
        "body_markdown": "Hi Priya,\n\nThe number is 412.\n\nKaan",
        "rationale": "Priya asked twice, by mail and chat",
        "sources": [
            SourceRef(
                kind="mail",
                id="m1",
                author="Priya Raman",
                author_address="priya@demo.example",
                timestamp=NOW,
                excerpt="confirm it?",
            )
        ],
    }
    base.update(kw)
    return ProposedAction(**base)


def test_round_trip_preserves_everything(tmp_path: Path) -> None:
    path = writer.write(action(), directory=tmp_path)
    restored, _ = parse(path)
    assert restored == action()


def test_the_filename_is_the_idempotency_key(tmp_path: Path) -> None:
    """Self-indexing queue: a duplicate is a filesystem collision, not a logic error."""
    path = writer.write(action(), directory=tmp_path)
    assert path.stem == action().id


def test_a_human_edit_to_the_body_is_what_gets_read_back(tmp_path: Path) -> None:
    path = writer.write(action(), directory=tmp_path)
    edited = path.read_text().replace("The number is 412.", "The number is 415, not 412.")
    path.write_text(edited)
    restored, _ = parse(path)
    assert "415" in restored.body_markdown
    assert "412." not in restored.body_markdown.replace("415, not 412.", "")


def test_a_human_edit_to_the_recipient_is_visible(tmp_path: Path) -> None:
    """FR-033 — the allowlist has to see what is actually there."""
    path = writer.write(action(), directory=tmp_path)
    path.write_text(path.read_text().replace("priya@demo.example", "someone@else.example"))
    restored, _ = parse(path)
    assert isinstance(restored.target, MailTarget)
    assert restored.target.to == ["someone@else.example"]


def test_the_editing_hint_is_stripped_not_sent(tmp_path: Path) -> None:
    path = writer.write(action(), directory=tmp_path)
    restored, _ = parse(path)
    assert "Edit it freely" not in restored.body_markdown


def test_frontmatter_carries_the_run_and_pinned_model(tmp_path: Path) -> None:
    """A reviewer must be able to tell which model wrote what they are approving."""
    path = writer.write(
        action(),
        directory=tmp_path,
        run_id="20260826-0900",
        model="gpt-5.5",
        model_version="2026-04-24",
    )
    _, meta = parse(path)
    assert meta["run_id"] == "20260826-0900"
    assert meta["model_version"] == "2026-04-24"


def test_an_issue_target_round_trips(tmp_path: Path) -> None:
    a = action(
        kind="create_issue",
        target=IssueTarget(repo="hkaanturgut/chief-of-staff-agents", title="Do the thing"),
    )
    path = writer.write(a, directory=tmp_path)
    restored, _ = parse(path)
    assert isinstance(restored.target, IssueTarget)
    assert restored.target.repo == "hkaanturgut/chief-of-staff-agents"


def test_an_unparseable_file_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text("---\nnot: [valid\n---\nbody")
    with pytest.raises(ProposalError):
        parse(path)


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    path = writer.write(action(), directory=tmp_path)
    path.write_text(path.read_text().replace("kind: reply_mail", "kind: launch_missile"))
    with pytest.raises(ProposalError, match="unknown kind"):
        parse(path)


def test_sources_survive_the_round_trip(tmp_path: Path) -> None:
    """Provenance in the file is what lets a reviewer check the reasoning."""
    path = writer.write(action(), directory=tmp_path)
    restored, _ = parse(path)
    assert restored.sources[0].author_address == "priya@demo.example"
    assert restored.sources[0].permalink is None
