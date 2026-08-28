"""Signal extraction — the three ingest agents.

Each source kind gets its own agent, because mail, chat, and calendar fail differently.
Threaded mail repeats an ask; chat omits its object; invites hide preparation in the body.
One prompt does all three badly, and the failure modes are what the specialisation is for.

Provenance is attached in code from the boundary models, not asked of the model. An agent
transcribing message ids will eventually transcribe one wrong, and a wrong id is a
citation that leads a reviewer nowhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from cos.agents.definitions import AgentDefinition
from cos.agents.runner import AgentRunner
from cos.logging import get_logger
from cos.models import CalendarEvent, ChatMessage, MailMessage, Signal, SourceRef
from cos.settings import ModelSettings
from cos.sources import refs

log = get_logger("agents.extract")

# Batch size per call. Small enough that one bad message cannot poison a whole run's
# extraction, large enough that a thread stays intact inside one call — which is what
# lets the agent extract a repeated ask once rather than once per message.
BATCH = 25

SIGNAL_TYPES = {"ask", "commitment", "fyi", "deadline", "meeting_prep", "conflict"}


class ExtractedSignal(BaseModel):
    """What an ingest agent returns for one signal.

    `source_indexes` rather than source objects: the agent points at the messages it was
    given, and code turns those pointers into `SourceRef`s. The model cannot mistranscribe
    an id it never had to type.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(description="ask, commitment, fyi, deadline, meeting_prep, or conflict")
    statement: str = Field(min_length=1)
    counterparty: str | None = None
    due_iso: str | None = Field(
        default=None,
        description="Only when a source states a date explicitly. Never inferred from "
        "tone. Leave empty for 'soon', 'ASAP', or 'when you get a chance'.",
    )
    ambiguous: bool = False
    source_indexes: list[int] = Field(
        min_length=1,
        description="Indexes of the messages this signal came from, as numbered in the "
        "input. Include every message that restates the same ask.",
    )


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signals: list[ExtractedSignal] = Field(default_factory=list)


def _parse_due(value: str | None, tz: ZoneInfo) -> datetime | None:
    """Parse an agent-supplied deadline, interpreting a bare date in the operator's zone.

    Models return "2026-08-28T17:00:00" or "2026-08-28" — dates as a person would write
    them, without an offset, because nobody told them one. Dropping those loses every
    deadline in the run, which is what happened the first time this ran end to end.

    A bare date means end of that working day where the operator is. That is what "by
    Friday" means to a person, and inventing 00:00 would make a Friday deadline look
    overdue on Thursday evening.
    """
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text[:10]), time(17, 0))
        except ValueError:
            log.warning("agent returned an unparseable due date; dropping it", value=value)
            return None
    if parsed.tzinfo is None:
        if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0):
            parsed = parsed.replace(hour=17)
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _to_signals(extraction: Extraction, sources: Sequence[SourceRef], tz: ZoneInfo) -> list[Signal]:
    """Turn agent output into contracts, discarding what cannot be trusted."""
    signals: list[Signal] = []
    for item in extraction.signals:
        if item.type not in SIGNAL_TYPES:
            log.warning("unknown signal type discarded", type=item.type)
            continue

        cited = [sources[i] for i in item.source_indexes if 0 <= i < len(sources)]
        if not cited:
            # Provenance is not optional. A signal citing nothing is unverifiable, and an
            # unverifiable to-do is a rumour.
            log.warning("signal without usable provenance discarded", statement=item.statement[:80])
            continue

        due = _parse_due(item.due_iso, tz)
        if item.ambiguous and due is not None:
            # The contract forbids this combination. Rather than failing the whole batch,
            # trust the hedge and drop the date — that is the safe direction.
            log.warning("ambiguous signal carried a date; dropping the date")
            due = None

        signals.append(
            Signal(
                type=item.type,  # type: ignore[arg-type]
                statement=item.statement,
                counterparty=item.counterparty,
                due=due,
                ambiguous=item.ambiguous,
                sources=cited,
            )
        )
    return signals


def render_mail(messages: Sequence[MailMessage]) -> str:
    lines: list[str] = []
    for index, m in enumerate(messages):
        who = "THE OPERATOR" if m.is_from_operator else (m.from_name or m.from_address)
        lines.append(
            f"[{index}] {m.received_at:%a %d %b %H:%M} from {who}"
            f"{' (thread ' + m.conversation_id[:12] + ')' if m.conversation_id else ''}"
        )
        lines.append(f"     subject: {m.subject}")
        lines.append(f"     {m.body_text[:1500]}")
        lines.append("")
    return "\n".join(lines)


def render_chat(messages: Sequence[ChatMessage]) -> str:
    lines: list[str] = []
    for index, c in enumerate(messages):
        who = "THE OPERATOR" if c.is_from_operator else (c.from_name or c.from_address or "unknown")
        lines.append(f"[{index}] {c.sent_at:%a %d %b %H:%M} {who}: {c.body_text[:800]}")
        if c.preceding_context:
            lines.append("     preceding context:")
            lines.extend(f"       - {line[:200]}" for line in c.preceding_context[-6:])
        lines.append("")
    return "\n".join(lines)


def render_events(events: Sequence[CalendarEvent]) -> str:
    lines: list[str] = []
    committed = sum((e.end - e.start).total_seconds() for e in events) / 3600.0
    lines.append(f"Hours already committed across this window: {committed:.1f}")
    lines.append("")
    for index, e in enumerate(events):
        lines.append(
            f"[{index}] {e.start:%a %d %b %H:%M}-{e.end:%H:%M} organiser {e.organizer}: {e.subject}"
        )
        if e.body_text:
            lines.append(f"     {e.body_text[:1000]}")
        lines.append("")
    return "\n".join(lines)


async def _extract_batch(
    runner: AgentRunner,
    definition: AgentDefinition,
    models: ModelSettings,
    prompt: str,
    sources: Sequence[SourceRef],
    tz: ZoneInfo,
) -> list[Signal]:
    extraction = await runner.call(
        agent=definition.name,
        tier=definition.model_tier(models),
        instructions=definition.instructions,
        prompt=prompt,
        schema=Extraction,
        stage="ingest",
        parent="chief-of-staff",
        label=f"{len(sources)} item(s)",
    )
    return _to_signals(extraction, sources, tz)


async def extract_mail(
    runner: AgentRunner,
    definition: AgentDefinition,
    models: ModelSettings,
    messages: Sequence[MailMessage],
    tz: ZoneInfo,
) -> list[Signal]:
    """Batched by thread-preserving order, so a repeated ask stays inside one call."""
    if not messages:
        return []
    batches = [messages[i : i + BATCH] for i in range(0, len(messages), BATCH)]
    results = await asyncio.gather(
        *(
            _extract_batch(
                runner,
                definition,
                models,
                render_mail(batch),
                [refs.from_mail(m) for m in batch],
                tz,
            )
            for batch in batches
        )
    )
    return [s for group in results for s in group]


async def extract_chat(
    runner: AgentRunner,
    definition: AgentDefinition,
    models: ModelSettings,
    messages: Sequence[ChatMessage],
    tz: ZoneInfo,
) -> list[Signal]:
    if not messages:
        return []
    batches = [messages[i : i + BATCH] for i in range(0, len(messages), BATCH)]
    results = await asyncio.gather(
        *(
            _extract_batch(
                runner,
                definition,
                models,
                render_chat(batch),
                [refs.from_chat(m) for m in batch],
                tz,
            )
            for batch in batches
        )
    )
    return [s for group in results for s in group]


async def extract_calendar(
    runner: AgentRunner,
    definition: AgentDefinition,
    models: ModelSettings,
    events: Sequence[CalendarEvent],
    tz: ZoneInfo,
) -> list[Signal]:
    if not events:
        return []
    return await _extract_batch(
        runner, definition, models, render_events(events), [refs.from_event(e) for e in events], tz
    )
