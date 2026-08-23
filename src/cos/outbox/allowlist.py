"""The kill switch.

Evaluated against the proposal file's CURRENT target at execution time, so editing a
recipient in the GitHub web editor does not slip past it (FR-033). A miss fails the
action before any provider call is made.

An empty allowlist permits nothing. That is the correct default, and it is why
`config/allowed_recipients.yaml` ships empty.
"""

from __future__ import annotations

from cos.models import ChatTarget, EventTarget, IssueTarget, MailTarget, ProposedAction
from cos.settings import AllowedRecipients


class NotAllowed(RuntimeError):
    """A recipient outside the allowlist. Never retried, never downgraded."""


def check(action: ProposedAction, allowed: AllowedRecipients) -> None:
    """Raise `NotAllowed` unless every recipient of this action is permitted."""
    target = action.target

    if isinstance(target, MailTarget):
        recipients = [*target.to, *target.cc]
        if not recipients:
            raise NotAllowed(f"{action.id}: no recipient")
        blocked = [r for r in recipients if not allowed.permits_address(r)]
        if blocked:
            raise NotAllowed(
                f"{action.id}: recipients not on the allowlist: {', '.join(blocked)}. "
                "Add them to config/allowed_recipients.yaml, deliberately."
            )
        return

    if isinstance(target, EventTarget):
        blocked = [a for a in target.attendees if not allowed.permits_address(a)]
        if blocked:
            raise NotAllowed(f"{action.id}: attendees not on the allowlist: {', '.join(blocked)}")
        return

    if isinstance(target, ChatTarget):
        if not allowed.permits_chat(target.chat_id):
            raise NotAllowed(f"{action.id}: chat {target.chat_id} is not on the allowlist")
        return

    if isinstance(target, IssueTarget):
        if not allowed.permits_repo(target.repo):
            raise NotAllowed(f"{action.id}: repository {target.repo} is not on the allowlist")
        return

    raise NotAllowed(f"{action.id}: unknown target type {type(target).__name__}")
