"""Mail retrieval and normalisation.

Returns `MailMessage`, never raw Graph JSON, so agents see a small stable shape and
fixtures swap in for free.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from cos.graph.client import GraphClient
from cos.logging import get_logger
from cos.models import MailMessage
from cos.sources.normalise import (
    address_of,
    addresses,
    body_text,
    name_of,
    parse_timestamp,
)
from cos.sources.window import Window

log = get_logger("sources.mail")

# Only the fields that survive normalisation. Asking Graph for less is faster, cheaper,
# and keeps recorded fixtures small enough to read in a diff.
SELECT = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,bodyPreview,body,webLink,isDraft"
)

DEFAULT_MAX_MESSAGES = 200


def normalise(
    payload: dict[str, Any], *, operator_address: str | None = None
) -> MailMessage | None:
    """One Graph message to a `MailMessage`, or None if it should not enter the pipeline."""
    received = parse_timestamp(payload.get("receivedDateTime"))
    if received is None:
        # No timestamp means no provenance, and provenance is not optional.
        log.warning("mail message without receivedDateTime skipped", id=payload.get("id"))
        return None
    if payload.get("isDraft"):
        return None

    sender = payload.get("from") or payload.get("sender")
    from_address = address_of(sender)
    operator = (operator_address or "").strip().lower()

    return MailMessage(
        id=str(payload["id"]),
        conversation_id=payload.get("conversationId"),
        internet_message_id=payload.get("internetMessageId"),
        subject=str(payload.get("subject") or ""),
        from_address=from_address,
        from_name=name_of(sender),
        to=addresses(payload.get("toRecipients")),
        cc=addresses(payload.get("ccRecipients")),
        received_at=received,
        body_text=body_text(payload.get("body"), payload.get("bodyPreview")),
        web_link=payload.get("webLink"),
        is_from_operator=bool(operator) and from_address.lower() == operator,
    )


def fetch(
    client: GraphClient,
    window: Window,
    *,
    operator_address: str | None = None,
    max_messages: int = DEFAULT_MAX_MESSAGES,
) -> list[MailMessage]:
    """Messages received inside the window, oldest first.

    Sent mail is included deliberately: the buried commitment — "I'll send the revised
    numbers by Friday" — is something the operator wrote, and a brief that only reads
    incoming mail cannot ever find it.
    """
    params = {
        "$select": SELECT,
        "$top": "50",
        "$orderby": "receivedDateTime desc",
        "$filter": (
            f"receivedDateTime ge {_graph_time(window.start)} "
            f"and receivedDateTime le {_graph_time(window.end)}"
        ),
    }

    messages: list[MailMessage] = []
    for folder in ("inbox", "sentitems"):
        raw: Iterable[dict[str, Any]] = client.paged(
            f"/me/mailFolders/{folder}/messages", params=params, max_items=max_messages
        )
        for payload in raw:
            message = normalise(payload, operator_address=operator_address)
            if message is not None:
                messages.append(message)

    messages.sort(key=lambda m: m.received_at)
    log.info("mail retrieved", count=len(messages), hours=round(window.hours, 1))
    return messages


def _graph_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
