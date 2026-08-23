"""Building `SourceRef` values — the unit of provenance.

Nothing enters the pipeline without one (Constitution IV), so this is the single place
excerpts are truncated and deep links are assembled.
"""

from __future__ import annotations

import re

from cos.models import EXCERPT_MAX, CalendarEvent, ChatMessage, MailMessage, SourceRef

_WS = re.compile(r"\s+")


def excerpt(text: str, limit: int = EXCERPT_MAX) -> str:
    """Collapse whitespace and truncate on a word boundary.

    Truncating mid-word looks like corruption in a pull request body, and the pull
    request body is the thing a human reads to decide whether to trust the item.
    """
    value = _WS.sub(" ", (text or "").strip())
    if len(value) <= limit:
        return value
    cut = value[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut + "…"


def teams_permalink(chat_id: str, message_id: str) -> str:
    return f"https://teams.microsoft.com/l/message/{chat_id}/{message_id}"


def from_mail(message: MailMessage) -> SourceRef:
    return SourceRef(
        kind="mail",
        id=message.id,
        thread_id=message.conversation_id,
        permalink=message.web_link,
        author=message.from_name or message.from_address,
        timestamp=message.received_at,
        excerpt=excerpt(message.body_text or message.subject),
    )


def from_chat(message: ChatMessage) -> SourceRef:
    return SourceRef(
        kind="chat",
        id=message.id,
        thread_id=message.chat_id,
        permalink=message.web_link or teams_permalink(message.chat_id, message.id),
        author=message.from_name or message.from_address or "unknown",
        timestamp=message.sent_at,
        excerpt=excerpt(message.body_text),
    )


def from_event(event: CalendarEvent) -> SourceRef:
    return SourceRef(
        kind="calendar",
        id=event.id,
        thread_id=None,
        permalink=event.web_link,
        author=event.organizer,
        timestamp=event.start,
        excerpt=excerpt(event.body_text or event.subject),
    )
