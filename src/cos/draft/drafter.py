"""The drafter.

One call per to-do that has an action. Produces a `ProposedAction` and never sends
anything — sending lives behind two gates in `outbox/executor.py`, and nothing here can
reach it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from cos.agents.definitions import AgentDefinition
from cos.agents.runner import AgentRunner, ModelTier
from cos.draft import voice
from cos.ids import action_id
from cos.logging import get_logger
from cos.models import (
    ChatTarget,
    EventTarget,
    IssueTarget,
    MailTarget,
    ProposedAction,
    TodoItem,
)

log = get_logger("draft.drafter")

AGENT = "drafter"

# The to-do's suggested action decides the kind of thing produced. The model writes the
# content; it does not get to choose the mechanism, because the mechanism determines
# which credential and which allowlist apply.
ACTION_TO_KIND = {
    "reply": "reply_mail",
    "schedule": "create_event",
    "delegate": "send_mail",
    "create_issue": "create_issue",
}


class Draft(BaseModel):
    """What the drafter returns for one to-do."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(default="", description="For mail or an event. Empty otherwise.")
    body_markdown: str = Field(min_length=1, description="The message itself.")
    recipients: list[str] = Field(
        default_factory=list,
        description="Addresses to send to. Take them from the sources; never invent one.",
    )
    risk: str = Field(description="low, medium, or high.")
    rationale: str = Field(
        min_length=1,
        description="Which specific sources drove this, by author and channel.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Anything you had to leave as a gap because the operator has not "
        "supplied it. Never fill these in with a guess.",
    )


def _risk(value: str, kind: str, recipients: Sequence[str], operator: str) -> str:
    """Take the model's assessment, then floor it on facts it cannot argue with.

    The model is asked to judge risk and mostly does it well. But "external recipient" is
    a property of the address, not a judgement call, so it is enforced here rather than
    hoped for.
    """
    level = value if value in {"low", "medium", "high"} else "high"
    operator_domain = operator.rsplit("@", 1)[-1].lower() if "@" in operator else ""
    external = any("@" in r and r.rsplit("@", 1)[1].lower() != operator_domain for r in recipients)
    if external and level == "low":
        return "medium"
    return level


def _target(
    todo: TodoItem, draft: Draft, kind: str, *, repo: str, now: datetime
) -> MailTarget | EventTarget | ChatTarget | IssueTarget:
    if kind in {"reply_mail", "send_mail"}:
        in_reply_to = next((s.id for s in reversed(todo.sources) if s.kind == "mail"), None)
        return MailTarget(
            to=draft.recipients,
            subject=draft.subject or todo.title,
            in_reply_to=in_reply_to if kind == "reply_mail" else None,
        )
    if kind == "create_event":
        start = todo.due or (now + timedelta(days=1))
        return EventTarget(
            subject=draft.subject or todo.title,
            start=start,
            end=start + timedelta(minutes=30),
            attendees=draft.recipients,
            body=draft.body_markdown,
        )
    if kind == "post_chat":
        chat_id = next((s.thread_id for s in todo.sources if s.kind == "chat"), None)
        return ChatTarget(chat_id=chat_id or "unknown")
    return IssueTarget(repo=repo, title=draft.subject or todo.title, labels=["chief-of-staff"])


def render_todo(todo: TodoItem, operator: str) -> str:
    lines = [
        f"You are drafting on behalf of: {operator}",
        "",
        f"TO-DO: {todo.title}",
        f"detail: {todo.detail}",
        f"owner: {todo.owner}",
        f"deadline: {todo.due.isoformat() if todo.due else 'none stated'}",
        f"urgency: {todo.urgency} — {todo.urgency_reason}",
        f"required action: {todo.suggested_action}",
        "",
        "SOURCES — take recipients and facts from these, and nowhere else:",
    ]
    for source in todo.sources:
        address = source.author_address or source.author
        lines.append(
            f"  - {source.kind} from {source.author} <{address}> {source.timestamp:%a %d %b %H:%M}"
        )
        lines.append(f"    {source.excerpt}")
    return "\n".join(lines)


async def draft_one(
    runner: AgentRunner,
    definition: AgentDefinition,
    tier: ModelTier,
    todo: TodoItem,
    *,
    operator: str,
    repo: str,
    now: datetime,
) -> ProposedAction | None:
    kind = ACTION_TO_KIND.get(todo.suggested_action)
    if kind is None:
        return None

    instructions = f"{definition.instructions}\n\n## The operator's voice\n\n{voice.load()}"
    result = await runner.call(
        agent=AGENT,
        tier=tier,
        instructions=instructions,
        prompt=render_todo(todo, operator),
        schema=Draft,
        stage="draft",
        parent="chief-of-staff",
        label=todo.title,
    )

    if kind in {"reply_mail", "send_mail"} and not result.recipients:
        # No recipient means no send. Better a to-do with no proposal than a proposal
        # addressed to a guess.
        log.warning("draft had no recipient; skipping", todo=todo.title[:60])
        return None

    target = _target(todo, result, kind, repo=repo, now=now)
    body = result.body_markdown
    if result.missing_information:
        gaps = "\n".join(f"- {item}" for item in result.missing_information)
        body = f"{body}\n\n<!-- NEEDS YOU BEFORE SENDING:\n{gaps}\n-->"

    return ProposedAction(
        id=action_id(todo.id, kind, target),
        todo_id=todo.id,
        kind=kind,  # type: ignore[arg-type]
        risk=_risk(result.risk, kind, result.recipients, operator),  # type: ignore[arg-type]
        target=target,
        body_markdown=body,
        rationale=result.rationale,
        sources=todo.sources,
    )


async def draft_all(
    runner: AgentRunner,
    definition: AgentDefinition,
    tier: ModelTier,
    todos: Sequence[TodoItem],
    *,
    operator: str,
    repo: str,
    now: datetime,
    max_actions: int,
) -> tuple[list[ProposedAction], int]:
    """Draft up to `max_actions`, highest urgency first.

    Returns the actions and how many actionable to-dos were left undrafted, so the pull
    request can say it stopped rather than appearing to have found nothing (FR-028).
    """
    candidates = [
        t for t in todos if t.suggested_action != "no_action" and not t.needs_human_judgment
    ]
    selected = candidates[:max_actions]
    skipped = len(candidates) - len(selected)

    results = await asyncio.gather(
        *(
            draft_one(runner, definition, tier, todo, operator=operator, repo=repo, now=now)
            for todo in selected
        )
    )
    actions = [a for a in results if a is not None]
    if skipped:
        log.warning("hit the per-run action cap", drafted=len(actions), skipped=skipped)
    return actions, skipped
