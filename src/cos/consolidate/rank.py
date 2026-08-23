"""Urgency, computed in code.

Three components, each scored 0.0–1.0, each multiplied by a configured weight, summed
and rounded to an integer in 0–100:

1. **due proximity** — only from a deadline a source explicitly stated
2. **sender importance** — only from `config/important_senders.yaml`
3. **distinct sources** — the same ask arriving three ways genuinely is more urgent

The model contributes `urgency_reason`, a sentence explaining the number it was handed.
It cannot change the number. That is what makes the polite-but-empty email rank low
reliably: no deadline, no weighted sender, one source, so there is nothing for it to
score on however urgent its phrasing sounds.

No model call. Ever (Constitution III).
"""

from __future__ import annotations

from datetime import datetime

from cos.models import Signal, SourceRef
from cos.settings import ImportantSenders, UrgencySettings


def due_component(due: datetime | None, now: datetime, settings: UrgencySettings) -> float:
    """1.0 at or inside the imminent horizon, decaying linearly to 0.0 at the far horizon.

    An overdue deadline scores 1.0 rather than overflowing: past due is maximally urgent,
    and there is nothing more urgent than that.
    """
    if due is None:
        return 0.0
    hours = (due - now).total_seconds() / 3600.0
    if hours <= settings.due_imminent_hours:
        return 1.0
    if hours >= settings.due_horizon_hours:
        return 0.0
    span = settings.due_horizon_hours - settings.due_imminent_hours
    return max(0.0, min(1.0, (settings.due_horizon_hours - hours) / span))


def sender_component(sources: list[SourceRef], senders: ImportantSenders) -> float:
    """The highest configured weight among the people who raised this.

    Absent from the list means zero. Not unimportant — simply contributing nothing.
    """
    return max((senders.weight_for(s.author) for s in sources), default=0.0)


def sources_component(sources: list[SourceRef], settings: UrgencySettings) -> float:
    """Distinct originating messages, saturating at the configured count."""
    distinct = len({(s.kind, s.id) for s in sources})
    return min(1.0, distinct / max(1, settings.sources_saturation))


def compute_urgency(
    *,
    due: datetime | None,
    sources: list[SourceRef],
    now: datetime,
    settings: UrgencySettings,
    senders: ImportantSenders,
) -> int:
    """The urgency score. Pure, bounded, and identical for identical input."""
    weights = settings.weights
    total = (
        due_component(due, now, settings) * weights.due_proximity
        + sender_component(sources, senders) * weights.sender_importance
        + sources_component(sources, settings) * weights.distinct_sources
    )
    return max(0, min(100, round(total)))


def explain_inputs(
    *,
    due: datetime | None,
    sources: list[SourceRef],
    now: datetime,
    settings: UrgencySettings,
    senders: ImportantSenders,
) -> dict[str, float | int | str | None]:
    """The component breakdown, handed to the model so it explains the real number.

    Without this the model would be guessing at why the score is what it is, and an
    urgency_reason that does not match the arithmetic is worse than none.
    """
    return {
        "due": due.isoformat() if due else None,
        "due_component": round(due_component(due, now, settings), 3),
        "sender_component": round(sender_component(sources, senders), 3),
        "distinct_sources": len({(s.kind, s.id) for s in sources}),
        "sources_component": round(sources_component(sources, settings), 3),
        "score": compute_urgency(
            due=due, sources=sources, now=now, settings=settings, senders=senders
        ),
    }


def earliest_explicit_due(signals: list[Signal]) -> datetime | None:
    """The earliest deadline any merged signal explicitly stated.

    Carried forward in code rather than restated by the model — letting the merge step
    re-express a date is how an inferred deadline would sneak back in.
    """
    dues = [s.due for s in signals if s.due is not None]
    return min(dues) if dues else None
