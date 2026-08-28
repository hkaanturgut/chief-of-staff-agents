"""Calendar retrieval and normalisation.

Invite bodies are kept, because they carry asks — one leg of the triple is a line buried
in a meeting invite.
"""

from __future__ import annotations

from typing import Any

from cos.graph.client import GraphClient
from cos.logging import get_logger
from cos.models import CalendarEvent
from cos.sources.normalise import (
    address_of,
    addresses,
    body_text,
    parse_graph_datetime,
)
from cos.sources.window import Window

log = get_logger("sources.calendar")

SELECT = "id,subject,start,end,organizer,attendees,body,bodyPreview,isAllDay,isCancelled,webLink"


def normalise(payload: dict[str, Any]) -> CalendarEvent | None:
    start = parse_graph_datetime(payload.get("start"))
    end = parse_graph_datetime(payload.get("end"))
    if start is None or end is None:
        log.warning("calendar event without start or end skipped", id=payload.get("id"))
        return None
    if end < start:
        log.warning("calendar event ends before it starts, skipped", id=payload.get("id"))
        return None

    return CalendarEvent(
        id=str(payload["id"]),
        subject=str(payload.get("subject") or ""),
        start=start,
        end=end,
        organizer=address_of(payload.get("organizer")),
        attendees=addresses(payload.get("attendees")),
        body_text=body_text(payload.get("body"), payload.get("bodyPreview")),
        is_all_day=bool(payload.get("isAllDay")),
        is_cancelled=bool(payload.get("isCancelled")),
        web_link=payload.get("webLink"),
    )


def fetch(client: GraphClient, window: Window, *, max_events: int = 100) -> list[CalendarEvent]:
    """Events across the look-ahead, expanded so recurrences appear as instances.

    `calendarView` rather than `/events`: a recurring series returned as a single master
    event tells you nothing about whether tomorrow is full.
    """
    params = {
        "$select": SELECT,
        "$top": "50",
        "$orderby": "start/dateTime",
        "startDateTime": window.calendar_start.isoformat(),
        "endDateTime": window.calendar_end.isoformat(),
    }
    events = [
        event
        for event in (
            normalise(p)
            for p in client.paged("/me/calendarView", params=params, max_items=max_events)
        )
        if event is not None and not event.is_cancelled
    ]
    log.info("calendar retrieved", count=len(events))
    return events
