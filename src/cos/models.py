"""Data contracts.

Every model here is `frozen` and forbids extra fields. Both matter:

- `extra="forbid"` means an agent that invents a field fails the run instead of quietly
  having the field discarded. That failure is the signal telling you the prompt is wrong.
- `frozen=True` means a downstream stage cannot mutate an upstream object, so provenance
  cannot be edited after the fact.

Invariants that the specification states in prose are validators here. An inferred
deadline is not a code-review comment, it is a `ValidationError`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------------------
# shared base
# --------------------------------------------------------------------------------------


class Contract(BaseModel):
    """Base for every wire contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_aware(value: datetime, field: str) -> datetime:
    """Reject naive datetimes at the boundary.

    A window comparison against a naive datetime is a bug that only shows up near
    midnight, on someone else's machine, during a demo.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware; got a naive datetime")
    return value


# --------------------------------------------------------------------------------------
# boundary models — what the sources normalise into
# --------------------------------------------------------------------------------------


class MailMessage(Contract):
    """A mail message, normalised. Agents never see raw Graph JSON."""

    id: str = Field(min_length=1)
    conversation_id: str | None = None
    internet_message_id: str | None = None
    subject: str = ""
    from_address: str
    from_name: str | None = None
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    received_at: datetime
    body_text: str = ""
    web_link: str | None = None
    is_from_operator: bool = False

    @field_validator("received_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "received_at")


class ChatMessage(Contract):
    """A Teams chat message, normalised.

    `preceding_context` exists because chat is low-context and full of implicit asks.
    Resolving "can you take a look?" needs the messages around it; without them the only
    options are to guess, which is forbidden, or to mark the signal ambiguous.
    """

    id: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    from_address: str | None = None
    from_name: str | None = None
    sent_at: datetime
    body_text: str = ""
    web_link: str | None = None
    is_from_operator: bool = False
    preceding_context: list[str] = Field(default_factory=list)

    @field_validator("sent_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "sent_at")


class CalendarEvent(Contract):
    """A calendar event, normalised. The body is retained because invites carry asks."""

    id: str = Field(min_length=1)
    subject: str = ""
    start: datetime
    end: datetime
    organizer: str
    attendees: list[str] = Field(default_factory=list)
    body_text: str = ""
    is_all_day: bool = False
    is_cancelled: bool = False
    web_link: str | None = None

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "start/end")

    @model_validator(mode="after")
    def _ordered(self) -> CalendarEvent:
        if self.end < self.start:
            raise ValueError("event end precedes its start")
        return self


# --------------------------------------------------------------------------------------
# contract models
# --------------------------------------------------------------------------------------

EXCERPT_MAX = 240


class SourceRef(Contract):
    """The unit of provenance. Nothing enters the pipeline without one."""

    kind: Literal["mail", "chat", "calendar"]
    id: str = Field(min_length=1)
    thread_id: str | None = None
    permalink: str | None = None
    author: str = ""
    timestamp: datetime
    excerpt: str = Field(default="", max_length=EXCERPT_MAX)

    @field_validator("timestamp")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "timestamp")

    def __hash__(self) -> int:
        # Identity is (kind, id), so merging two signals that cite the same message
        # deduplicates the citation naturally.
        return hash((self.kind, self.id))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SourceRef):
            return NotImplemented
        return (self.kind, self.id) == (other.kind, other.id)


SignalType = Literal["ask", "commitment", "fyi", "deadline", "meeting_prep", "conflict"]


class Signal(Contract):
    """One extracted observation from one source."""

    type: SignalType
    statement: str = Field(min_length=1)
    counterparty: str | None = None
    due: datetime | None = None
    ambiguous: bool = False
    sources: Annotated[list[SourceRef], Field(min_length=1)]

    @field_validator("due")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v, "due")

    @model_validator(mode="after")
    def _no_invented_deadline(self) -> Signal:
        """A deadline cannot be both uncertain and stated.

        This is the "soon-ish" trap turned into a contract. An agent that hedges with
        `ambiguous` and then supplies a date anyway has invented the date.
        """
        if self.ambiguous and self.due is not None:
            raise ValueError(
                "an ambiguous signal cannot carry a due date; "
                "a deadline is recorded only when a source states it explicitly"
            )
        return self


Owner = Literal["me", "delegate", "waiting"]
SuggestedAction = Literal["reply", "schedule", "delegate", "create_issue", "no_action"]


class TodoItem(Contract):
    """One deduplicated unit of work, formed by merging signals about the same ask."""

    id: str = Field(min_length=26, max_length=26)
    title: str = Field(min_length=1, max_length=120)
    detail: str = ""
    owner: Owner
    due: datetime | None = None
    urgency: int = Field(ge=0, le=100)
    urgency_reason: str = ""
    suggested_action: SuggestedAction
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_judgment: bool = False
    sources: Annotated[list[SourceRef], Field(min_length=1)]
    seen_in_runs: int = Field(default=1, ge=1)

    @field_validator("due")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v, "due")

    @model_validator(mode="after")
    def _judgment_blocks_action(self) -> TodoItem:
        """No draft is written for an item the system does not understand (FR-018)."""
        if self.needs_human_judgment and self.suggested_action != "no_action":
            raise ValueError(
                "needs_human_judgment requires suggested_action='no_action'; "
                f"got {self.suggested_action!r}"
            )
        return self


# --------------------------------------------------------------------------------------
# action targets — discriminated, not a bare dict
# --------------------------------------------------------------------------------------
# The executor branches on these. An untyped payload is exactly where a wrong recipient
# hides, so each variant is a model and the allowlist check reads typed fields.


class MailTarget(Contract):
    to: Annotated[list[str], Field(min_length=1)]
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    in_reply_to: str | None = None


class EventTarget(Contract):
    subject: str = Field(min_length=1)
    start: datetime
    end: datetime
    attendees: list[str] = Field(default_factory=list)
    body: str = ""

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "start/end")


class ChatTarget(Contract):
    chat_id: str = Field(min_length=1)
    chat_topic: str | None = None


class IssueTarget(Contract):
    repo: str = Field(min_length=1)
    title: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)


ActionTarget = MailTarget | EventTarget | ChatTarget | IssueTarget

ActionKind = Literal["send_mail", "reply_mail", "create_event", "post_chat", "create_issue"]
Risk = Literal["low", "medium", "high"]

_TARGET_FOR_KIND: dict[str, type[Contract]] = {
    "send_mail": MailTarget,
    "reply_mail": MailTarget,
    "create_event": EventTarget,
    "post_chat": ChatTarget,
    "create_issue": IssueTarget,
}


class ProposedAction(Contract):
    """One concrete outbound act the system would perform. It never performs it."""

    id: str = Field(min_length=26, max_length=26)
    todo_id: str = Field(min_length=26, max_length=26)
    kind: ActionKind
    risk: Risk
    target: ActionTarget
    body_markdown: str = ""
    rationale: str = Field(min_length=1)
    sources: Annotated[list[SourceRef], Field(min_length=1)]

    @model_validator(mode="after")
    def _target_matches_kind(self) -> ProposedAction:
        expected = _TARGET_FOR_KIND[self.kind]
        if not isinstance(self.target, expected):
            raise ValueError(
                f"kind={self.kind!r} requires a {expected.__name__}; "
                f"got {type(self.target).__name__}"
            )
        return self


# --------------------------------------------------------------------------------------
# run bookkeeping
# --------------------------------------------------------------------------------------


class RunManifest(Contract):
    """What one run did. Becomes the header of the pull request description."""

    run_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime | None = None
    window_start: datetime
    window_end: datetime
    sources_requested: list[str] = Field(default_factory=list)
    sources_succeeded: list[str] = Field(default_factory=list)
    # Not an error. A partial brief that says it is partial beats no brief — but it must
    # say so, or the reader is misled about coverage (FR-009, R-012).
    sources_failed: list[str] = Field(default_factory=list)
    messages_in: int = 0
    signals: int = 0
    clusters: int = 0
    todos: int = 0
    actions: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    wall_seconds: float = 0.0
    dry_run: bool = True

    @field_validator("started_at", "window_start", "window_end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "manifest timestamps")


class ModelCallLog(Contract):
    """One line in state/runs/<run_id>.jsonl.

    Prompts are hashed rather than stored. The hash proves which prompt ran and detects
    drift between rehearsal and performance, without committing mailbox content to a
    public repository.
    """

    run_id: str
    agent: str
    model: str
    model_version: str
    prompt_hash: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    attempt: int = 1
    validation_error: str | None = None


class LedgerEntry(Contract):
    """Proof that an action was performed. The authority on what has already been sent."""

    action_id: str = Field(min_length=26, max_length=26)
    todo_id: str
    kind: ActionKind
    performed_at: datetime
    receipt_id: str | None = None
    pr_number: int | None = None
    run_id: str | None = None

    @field_validator("performed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "performed_at")


class Ledger(Contract):
    entries: list[LedgerEntry] = Field(default_factory=list)
