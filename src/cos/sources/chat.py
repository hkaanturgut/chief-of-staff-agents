"""Teams chat retrieval and normalisation.

Implemented and unused, on purpose.

Chat is unavailable to this deployment for two independent reasons, and both are
platform refusals rather than gaps in this code:

  * A **consumer** Microsoft account does not expose Teams chat through Graph at all.
  * An **application** identity needs Microsoft's protected API approval to read chat
    messages, which takes days to weeks.

See `docs/decisions.md` B-001 and research R-012. `collect()` reports chat as an
unavailable source, and the brief says so at runtime rather than quietly under-reporting.

This module exists so that pointing the system at a work tenant with delegated auth is a
configuration change rather than a development task. It is exercised by tests against
recorded payloads, so it does not rot while it waits.
"""

from __future__ import annotations

from typing import Any

from cos.graph.client import GraphClient
from cos.logging import get_logger
from cos.models import ChatMessage
from cos.sources.normalise import body_text, parse_timestamp
from cos.sources.window import Window

log = get_logger("sources.chat")

SELECT = "id,chatId,from,createdDateTime,body,webLink"

# Chat is low-context: the thing being asked about usually sits in an earlier message.
# Without these the extraction agent has no honest way to resolve "can you take a look?",
# and guessing is forbidden.
CONTEXT_MESSAGES = 8


def normalise(
    payload: dict[str, Any],
    *,
    chat_id: str,
    operator_address: str | None = None,
    preceding: list[str] | None = None,
) -> ChatMessage | None:
    sent = parse_timestamp(payload.get("createdDateTime"))
    if sent is None:
        log.warning("chat message without a timestamp skipped", id=payload.get("id"))
        return None

    sender = (payload.get("from") or {}).get("user") or {}
    address = str(sender.get("email") or sender.get("userPrincipalName") or "") or None
    name = str(sender.get("displayName") or "") or None
    if not address and not name:
        # System messages — joins, renames, reactions — have no user. They are not asks.
        return None

    text = body_text(payload.get("body"))
    if not text.strip():
        return None

    operator = (operator_address or "").strip().lower()
    return ChatMessage(
        id=str(payload["id"]),
        chat_id=chat_id,
        from_address=address,
        from_name=name,
        sent_at=sent,
        body_text=text,
        web_link=payload.get("webLink"),
        is_from_operator=bool(operator) and (address or "").lower() == operator,
        preceding_context=preceding or [],
    )


def fetch(
    client: GraphClient,
    window: Window,
    *,
    operator_address: str | None = None,
    max_chats: int = 20,
    max_messages_per_chat: int = 50,
) -> list[ChatMessage]:
    """Messages across the operator's chats, inside the window, oldest first.

    Graph does not support a server-side date filter on chat messages, so the window is
    applied here. `max_messages_per_chat` bounds the cost of a very busy chat.
    """
    messages: list[ChatMessage] = []

    for chat in client.paged("/me/chats", params={"$top": "20"}, max_items=max_chats):
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            continue

        raw = list(
            client.paged(
                f"/me/chats/{chat_id}/messages",
                params={"$top": "50", "$select": SELECT},
                max_items=max_messages_per_chat,
            )
        )
        # Oldest first, so preceding context is genuinely preceding.
        raw.reverse()

        context: list[str] = []
        for payload in raw:
            message = normalise(
                payload,
                chat_id=chat_id,
                operator_address=operator_address,
                preceding=context[-CONTEXT_MESSAGES:],
            )
            if message is None:
                continue
            context.append(f"{message.from_name or 'unknown'}: {message.body_text[:200]}")
            if window.start <= message.sent_at <= window.end:
                messages.append(message)

    messages.sort(key=lambda m: m.sent_at)
    log.info("chat retrieved", count=len(messages))
    return messages
