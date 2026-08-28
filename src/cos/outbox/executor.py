"""Performing approved actions.

The most dangerous code in the repository. Every control lives here rather than in
workflow YAML, because a control implemented in a workflow is a control a local run
bypasses — and `cos execute` must traverse exactly the same path `execute.yml` does.

Order, per contracts/workflows.md, and it is not negotiable:

    read -> allowlist -> ledger -> per-run cap -> dry run -> perform -> receipt

The allowlist comes before the ledger so that a forbidden recipient fails even if the
action was somehow already recorded, and both come before anything that can reach a
provider.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cos.graph.client import GraphClient, GraphError
from cos.logging import get_logger
from cos.models import ChatTarget, EventTarget, IssueTarget, MailTarget, ProposedAction
from cos.outbox import allowlist as allowlist_module
from cos.outbox import writer
from cos.outbox.ledger import LedgerFile
from cos.outbox.reader import ProposalError, parse
from cos.settings import AllowedRecipients, RunSettings

log = get_logger("outbox.executor")


class DryRunViolation(RuntimeError):
    """Raised if anything tries to reach a provider during a dry run."""


@dataclass
class Outcome:
    action_id: str
    status: str  # sent | skipped | blocked | failed | dry_run
    detail: str = ""
    receipt_id: str | None = None
    path: Path | None = None


@dataclass
class ExecutionReport:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def sent(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "sent"]

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status in {"failed", "blocked"}]

    def add(self, outcome: Outcome) -> Outcome:
        self.outcomes.append(outcome)
        return outcome


def _mail_payload(target: MailTarget, body: str) -> dict[str, object]:
    return {
        "message": {
            "subject": target.subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in target.to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in target.cc],
        },
        "saveToSentItems": True,
    }


def _event_payload(target: EventTarget, body: str) -> dict[str, object]:
    return {
        "subject": target.subject,
        "body": {"contentType": "Text", "content": body},
        "start": {"dateTime": target.start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": target.end.isoformat(), "timeZone": "UTC"},
        "attendees": [
            {"emailAddress": {"address": a}, "type": "required"} for a in target.attendees
        ],
    }


class Executor:
    """Performs actions, or refuses to.

    Under `dry_run` no client is constructed at all. Merely skipping the call would leave
    a live transport one bad branch away from firing; refusing to build one means the
    capability does not exist during a rehearsal.
    """

    def __init__(
        self,
        *,
        run: RunSettings,
        allowed: AllowedRecipients,
        ledger: LedgerFile,
        client_factory: Callable[[], GraphClient] | None = None,
        github: Callable[[IssueTarget, str], str] | None = None,
        run_id: str = "",
        pr_number: int | None = None,
    ) -> None:
        self.run = run
        self.allowed = allowed
        self.ledger = ledger
        self._client_factory = client_factory
        self._github = github
        self.run_id = run_id
        self.pr_number = pr_number
        self._client: GraphClient | None = None
        self._performed = 0

    def _graph(self) -> GraphClient:
        if self.run.dry_run:
            raise DryRunViolation(
                "a Graph client was requested during a dry run. Nothing may reach a "
                "provider while dry_run is set."
            )
        if self._client is None:
            if self._client_factory is None:
                raise RuntimeError("no Graph client factory configured")
            self._client = self._client_factory()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- the ordered gauntlet ----------------------------------------------------------

    def execute_path(self, path: Path) -> Outcome:
        try:
            action, meta = parse(path)
        except ProposalError as exc:
            return self._fail(path, None, str(exc))

        return self.execute(action, path=path, meta=meta)

    def execute(
        self,
        action: ProposedAction,
        *,
        path: Path | None = None,
        meta: dict[str, object] | None = None,
    ) -> Outcome:
        # 1. allowlist — before the ledger, and before anything that can reach a provider.
        try:
            allowlist_module.check(action, self.allowed)
        except allowlist_module.NotAllowed as exc:
            log.error("blocked by the allowlist", action_id=action.id, detail=str(exc))
            return self._fail(path, action, str(exc), status="blocked")

        # 2. ledger — a re-run must never double-send.
        if not self.ledger.check_and_reserve(action.id):
            return Outcome(action.id, "skipped", "already in the ledger", path=path)

        # 3. per-run cap — a hard stop, not a warning.
        if self._performed >= self.run.max_actions_per_run:
            return Outcome(
                action.id,
                "skipped",
                f"per-run cap of {self.run.max_actions_per_run} reached",
                path=path,
            )

        # 4. dry run — log fully, perform nothing.
        if self.run.dry_run:
            log.info(
                "dry run: would perform",
                action_id=action.id,
                kind=action.kind,
                risk=action.risk,
                target=action.target.model_dump(mode="json"),
            )
            return Outcome(action.id, "dry_run", "dry_run is set", path=path)

        # 5. perform.
        try:
            receipt = self._perform(action)
        except (GraphError, RuntimeError) as exc:
            log.error("action failed", action_id=action.id, error=str(exc)[:300])
            return self._fail(path, action, str(exc))

        self._performed += 1
        self.ledger.record_send(
            action_id=action.id,
            todo_id=action.todo_id,
            kind=action.kind,
            receipt_id=receipt,
            pr_number=self.pr_number,
            run_id=self.run_id,
        )
        if path is not None:
            self._move(
                path,
                action,
                writer.SENT,
                status="sent",
                extra={
                    "sent_at": datetime.now(UTC).isoformat(),
                    "receipt_id": receipt,
                },
            )
        log.info("performed", action_id=action.id, kind=action.kind, receipt=receipt)
        return Outcome(action.id, "sent", receipt_id=receipt, path=path)

    def _perform(self, action: ProposedAction) -> str:
        target = action.target
        body = action.body_markdown

        if action.kind in {"send_mail", "reply_mail"}:
            assert isinstance(target, MailTarget)
            client = self._graph()
            if action.kind == "reply_mail" and target.in_reply_to:
                created = client.post(
                    f"/me/messages/{target.in_reply_to}/createReply", {"comment": body}
                )
                message_id = str(created.get("id", ""))
                client.post(f"/me/messages/{message_id}/send", {})
                return message_id
            client.post("/me/sendMail", _mail_payload(target, body))
            # sendMail returns 202 with no body, so there is no provider id to record.
            # The action id remains the idempotency key; this records that it happened.
            return f"sendMail:{action.id}"

        if action.kind == "create_event":
            assert isinstance(target, EventTarget)
            created = self._graph().post("/me/events", _event_payload(target, body))
            return str(created.get("id", ""))

        if action.kind == "post_chat":
            assert isinstance(target, ChatTarget)
            created = self._graph().post(
                f"/chats/{target.chat_id}/messages",
                {"body": {"contentType": "text", "content": body}},
            )
            return str(created.get("id", ""))

        if action.kind == "create_issue":
            assert isinstance(target, IssueTarget)
            if self._github is None:
                raise RuntimeError("no GitHub handler configured for create_issue")
            return self._github(target, body)

        raise RuntimeError(f"unknown action kind {action.kind}")

    # --- outcomes ----------------------------------------------------------------------

    def _fail(
        self,
        path: Path | None,
        action: ProposedAction | None,
        detail: str,
        *,
        status: str = "failed",
    ) -> Outcome:
        if path is not None and action is not None:
            self._move(
                path,
                action,
                writer.FAILED,
                status=status,
                extra={
                    "failed_at": datetime.now(UTC).isoformat(),
                    "error": detail[:1000],
                },
            )
        return Outcome(
            action.id if action else path.name if path else "?", status, detail, path=path
        )

    @staticmethod
    def _move(
        path: Path,
        action: ProposedAction,
        directory: Path,
        *,
        status: str,
        extra: dict[str, object],
    ) -> Path:
        destination = writer.write(action, directory=directory, status=status, extra=extra)
        path.unlink(missing_ok=True)
        return destination


def execute_pending(executor: Executor, *, directory: Path | None = None) -> ExecutionReport:
    """Every pending proposal, in filename order.

    One failure never stops the rest: an action that fails is moved aside and the run
    continues (FR-035).
    """
    report = ExecutionReport()
    target = directory or writer.PENDING
    for path in sorted(target.glob("*.md")):
        report.add(executor.execute_path(path))
    return report
