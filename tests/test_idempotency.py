"""Executing the same action twice must send exactly once.

This is the reason the ledger exists. A workflow can be re-triggered by a retry, a
re-run, or a second push, and without this the system sends the same apology twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cos.models import MailTarget, ProposedAction, SourceRef
from cos.outbox import writer
from cos.outbox.executor import DryRunViolation, Executor, execute_pending
from cos.outbox.ledger import LedgerFile
from cos.settings import AllowedRecipients, RunSettings

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
ALLOWED = AllowedRecipients(addresses=["priya@demo.example"])
LIVE = RunSettings(dry_run=False, max_actions_per_run=5)


def action(
    id_: str = "01JX8ZQ4K7N2M5P8R3T6V9W1XY", to: str = "priya@demo.example"
) -> ProposedAction:
    return ProposedAction(
        id=id_,
        todo_id="01JX7YP3J6M1L4N7Q2S5U8V0WX",
        kind="send_mail",
        risk="low",
        target=MailTarget(to=[to], subject="Re: renewal"),
        body_markdown="Hi Priya,\n\nThe number is 412.\n\nKaan",
        rationale="Priya asked twice",
        sources=[SourceRef(kind="mail", id="m1", author="Priya", timestamp=NOW)],
    )


class CountingGraph:
    """Counts sends. Never touches a network."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, Any]] = []

    def post(self, path: str, body: Any, **_: Any) -> dict[str, Any]:
        self.sends.append((path, body))
        return {"id": f"receipt-{len(self.sends)}"}

    def close(self) -> None:
        pass


@pytest.fixture
def ledger(tmp_path: Path) -> LedgerFile:
    return LedgerFile(tmp_path / "ledger.json")


def executor(ledger: LedgerFile, graph: CountingGraph, run: RunSettings = LIVE) -> Executor:
    return Executor(run=run, allowed=ALLOWED, ledger=ledger, client_factory=lambda: graph)  # type: ignore[arg-type]


# --- the core guarantee -----------------------------------------------------------------


def test_executing_the_same_action_twice_sends_once(ledger: LedgerFile) -> None:
    graph = CountingGraph()
    ex = executor(ledger, graph)
    first = ex.execute(action())
    second = ex.execute(action())
    assert first.status == "sent"
    assert second.status == "skipped"
    assert len(graph.sends) == 1


def test_a_fresh_executor_reading_the_same_ledger_still_skips(ledger: LedgerFile) -> None:
    """A re-run is a new process. The ledger, not memory, is what remembers."""
    graph = CountingGraph()
    executor(ledger, graph).execute(action())
    reloaded = LedgerFile(ledger.path)
    executor(reloaded, graph).execute(action())
    assert len(graph.sends) == 1


def test_the_receipt_is_recorded(ledger: LedgerFile) -> None:
    executor(ledger, CountingGraph()).execute(action())
    entries = LedgerFile(ledger.path).entries
    assert len(entries) == 1
    assert entries[0].receipt_id


def test_an_unreadable_ledger_refuses_to_run(tmp_path: Path) -> None:
    """Reading a corrupt ledger as empty would re-send everything it ever recorded."""
    path = tmp_path / "ledger.json"
    path.write_text("{ this is not json")
    with pytest.raises(RuntimeError, match="Refusing to run"):
        LedgerFile(path)


# --- the allowlist ----------------------------------------------------------------------


def test_an_unlisted_recipient_is_blocked_before_any_provider_call(ledger: LedgerFile) -> None:
    graph = CountingGraph()
    outcome = executor(ledger, graph).execute(action(to="stranger@elsewhere.example"))
    assert outcome.status == "blocked"
    assert graph.sends == []


def test_a_blocked_action_is_not_recorded_as_sent(ledger: LedgerFile) -> None:
    executor(ledger, CountingGraph()).execute(action(to="stranger@elsewhere.example"))
    assert LedgerFile(ledger.path).entries == []


def test_an_empty_allowlist_permits_nothing(ledger: LedgerFile) -> None:
    graph = CountingGraph()
    ex = Executor(
        run=LIVE,
        allowed=AllowedRecipients(),
        ledger=ledger,
        client_factory=lambda: graph,  # type: ignore[arg-type]
    )
    assert ex.execute(action()).status == "blocked"
    assert graph.sends == []


def test_a_recipient_edited_into_the_file_is_still_checked(
    ledger: LedgerFile, tmp_path: Path
) -> None:
    """FR-033. The allowlist reads the file's current contents, not what was proposed."""
    pending = tmp_path / "pending"
    path = writer.write(action(), directory=pending)
    path.write_text(path.read_text().replace("priya@demo.example", "stranger@elsewhere.example"))

    graph = CountingGraph()
    outcome = executor(ledger, graph).execute_path(path)
    assert outcome.status == "blocked"
    assert graph.sends == []


# --- dry run -----------------------------------------------------------------------------


def test_dry_run_performs_nothing(ledger: LedgerFile) -> None:
    graph = CountingGraph()
    dry = RunSettings(dry_run=True, max_actions_per_run=5)
    outcome = executor(ledger, graph, dry).execute(action())
    assert outcome.status == "dry_run"
    assert graph.sends == []
    assert LedgerFile(ledger.path).entries == []


def test_dry_run_refuses_to_construct_a_client_at_all(ledger: LedgerFile) -> None:
    """Skipping the call would leave a live transport one bad branch from firing."""
    dry = RunSettings(dry_run=True, max_actions_per_run=5)
    ex = Executor(run=dry, allowed=ALLOWED, ledger=ledger, client_factory=CountingGraph)  # type: ignore[arg-type]
    with pytest.raises(DryRunViolation):
        ex._graph()


# --- the per-run cap ----------------------------------------------------------------------


def test_the_cap_is_a_hard_stop(ledger: LedgerFile) -> None:
    graph = CountingGraph()
    capped = RunSettings(dry_run=False, max_actions_per_run=2)
    ex = executor(ledger, graph, capped)
    ids = ["01JX8ZQ4K7N2M5P8R3T6V9W1X" + c for c in "ABCD"]
    outcomes = [ex.execute(action(id_=i)) for i in ids]
    assert [o.status for o in outcomes] == ["sent", "sent", "skipped", "skipped"]
    assert len(graph.sends) == 2


# --- failure handling ---------------------------------------------------------------------


def test_a_failure_moves_the_file_aside_and_the_run_continues(
    ledger: LedgerFile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = tmp_path / "pending"
    monkeypatch.setattr(writer, "SENT", tmp_path / "sent")
    monkeypatch.setattr(writer, "FAILED", tmp_path / "failed")
    monkeypatch.setattr(writer, "PENDING", pending)

    ids = ["01JX8ZQ4K7N2M5P8R3T6V9W1X" + c for c in "AB"]
    for i in ids:
        writer.write(action(id_=i), directory=pending)

    class Exploding(CountingGraph):
        def post(self, path: str, body: Any, **_: Any) -> dict[str, Any]:
            if len(self.sends) == 0:
                self.sends.append((path, body))
                raise RuntimeError("provider said no")
            return super().post(path, body)

    graph = Exploding()
    report = execute_pending(executor(ledger, graph), directory=pending)
    assert len(report.outcomes) == 2
    assert {o.status for o in report.outcomes} == {"failed", "sent"}
    assert (tmp_path / "failed").exists()
    assert (tmp_path / "sent").exists()


def test_a_successful_send_moves_the_file_to_sent(
    ledger: LedgerFile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = tmp_path / "pending"
    monkeypatch.setattr(writer, "SENT", tmp_path / "sent")
    path = writer.write(action(), directory=pending)
    executor(ledger, CountingGraph()).execute_path(path)
    assert not path.exists()
    moved = list((tmp_path / "sent").glob("*.md"))
    assert len(moved) == 1
    assert "status: sent" in moved[0].read_text()
    assert "receipt_id" in moved[0].read_text()
