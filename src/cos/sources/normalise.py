"""Shared normalisation helpers for the source boundary."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TAG = re.compile(r"<[^>]+>")
_STYLE_OR_SCRIPT = re.compile(r"<(style|script)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
# Paragraph-level breaks become blank lines; line-level breaks become single newlines.
# Mail reads as paragraphs, and flattening them makes a long thread unreadable in the
# pull request body.
_PARAGRAPH_END = re.compile(r"</(p|div|h[1-6])\s*>", re.IGNORECASE)
_LINE_END = re.compile(r"<br\s*/?>|</(tr|li)\s*>", re.IGNORECASE)
_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&zwnj;": "",
}
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")


def html_to_text(html: str) -> str:
    """Strip HTML to plain text, once, at the boundary.

    Agents never see markup. It costs tokens, it carries no signal for extraction, and a
    tracking pixel's alt text is not an ask.
    """
    if not html:
        return ""
    text = _STYLE_OR_SCRIPT.sub(" ", html)
    text = _PARAGRAPH_END.sub("\n\n", text)
    text = _LINE_END.sub("\n", text)
    text = _TAG.sub("", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    text = _TRAILING_WS.sub("\n", text)
    return _BLANK_RUN.sub("\n\n", text).strip()


def body_text(body: dict[str, Any] | None, preview: str | None = None) -> str:
    """Take a Graph `body` object down to plain text, falling back to the preview."""
    if not body:
        return (preview or "").strip()
    content = str(body.get("content") or "")
    if str(body.get("contentType") or "").lower() == "html":
        return html_to_text(content)
    return content.strip() or (preview or "").strip()


def parse_graph_datetime(value: dict[str, Any] | None) -> datetime | None:
    """Parse Graph's `{dateTime, timeZone}` pair into an aware datetime.

    Calendar endpoints return a naive `dateTime` alongside a separate `timeZone`, unlike
    mail which carries a `Z` suffix. Treating the calendar shape like the mail shape
    yields naive datetimes, which the contracts reject — correctly, since a naive
    comparison against a window is a bug that only appears near midnight.
    """
    if not value:
        return None
    parsed = parse_timestamp(str(value.get("dateTime") or "") or None)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        return parsed
    zone = str(value.get("timeZone") or "UTC")
    try:
        tz = UTC if zone.upper() == "UTC" else ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        # Graph occasionally returns Windows zone names. UTC is wrong by hours at worst;
        # a naive datetime is wrong in a way that fails validation and kills the run.
        tz = UTC
    return parsed.replace(tzinfo=tz)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a Graph timestamp into an aware datetime.

    Graph emits `Z`; `fromisoformat` wants an offset on older Pythons, and calendar
    values can carry sub-second precision beyond what it accepts.
    """
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        fraction, sign, offset = tail.partition("+") if "+" in tail else tail.partition("-")
        text = f"{head}.{fraction[:6]}{sign}{offset}" if sign else f"{head}.{fraction[:6]}"
    parsed = datetime.fromisoformat(text)
    return parsed


def address_of(recipient: dict[str, Any] | None) -> str:
    if not recipient:
        return ""
    email = recipient.get("emailAddress") or {}
    return str(email.get("address") or "").strip()


def name_of(recipient: dict[str, Any] | None) -> str | None:
    if not recipient:
        return None
    email = recipient.get("emailAddress") or {}
    name = str(email.get("name") or "").strip()
    return name or None


def addresses(recipients: list[dict[str, Any]] | None) -> list[str]:
    return [a for a in (address_of(r) for r in (recipients or [])) if a]
