"""The ledger — the authority on what has already been sent.

Append-only JSON in the repository. `state/ledger.json` is the state, and git is the
audit trail; there is no database, because a second source of truth is a second thing
that can disagree.

`check_and_reserve` is the only path to a send. That is the whole design: a workflow can
be re-triggered by a retry, a re-run, or a second push, and this file is what stands
between the system and sending the same apology twice.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cos.logging import get_logger
from cos.models import Ledger, LedgerEntry
from cos.settings import REPO_ROOT

log = get_logger("outbox.ledger")

LEDGER_PATH = REPO_ROOT / "state" / "ledger.json"


class LedgerFile:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or LEDGER_PATH
        self._ledger = self._read()

    def _read(self) -> Ledger:
        if not self.path.exists():
            return Ledger()
        try:
            return Ledger.model_validate(json.loads(self.path.read_text()))
        except Exception as exc:
            # Refuse to proceed rather than start from an empty ledger. An unreadable
            # ledger read as empty would re-send everything it ever recorded.
            raise RuntimeError(
                f"{self.path} is unreadable ({exc}). Refusing to run: an empty ledger "
                "would re-send every action ever recorded."
            ) from exc

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._ledger.entries)

    def contains(self, action_id: str) -> bool:
        return any(e.action_id == action_id for e in self._ledger.entries)

    def check_and_reserve(self, action_id: str) -> bool:
        """True if this action may proceed. False if it has already been performed.

        Reservation happens on `record`, after the provider confirms. Recording before
        the send would mean a crash mid-send silently swallows the action forever, and a
        lost send is easier to notice than a double send is to undo — but only if the
        ledger reflects reality rather than intent.
        """
        if self.contains(action_id):
            log.info("already performed; skipping", action_id=action_id)
            return False
        return True

    def record(self, entry: LedgerEntry) -> None:
        if self.contains(entry.action_id):
            return
        self._ledger = Ledger(entries=[*self._ledger.entries, entry])
        self.flush()

    def record_send(
        self,
        *,
        action_id: str,
        todo_id: str,
        kind: str,
        receipt_id: str | None,
        pr_number: int | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            action_id=action_id,
            todo_id=todo_id,
            kind=kind,  # type: ignore[arg-type]
            performed_at=now or datetime.now(UTC),
            receipt_id=receipt_id,
            pr_number=pr_number,
            run_id=run_id,
        )
        self.record(entry)
        return entry

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._ledger.model_dump(mode="json"), indent=1, sort_keys=False) + "\n"
        )
