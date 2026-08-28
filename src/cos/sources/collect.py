"""Gathering the sources for a run.

One place decides which sources to query and what happens when one of them fails. A
source failing is not an error: a partial brief that says it is partial beats no brief.
But it must say so, or the reader is misled about coverage (FR-009).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cos.graph.client import GraphClient, GraphError
from cos.logging import get_logger
from cos.manifest import RunRecorder
from cos.models import CalendarEvent, ChatMessage, MailMessage
from cos.sources import calendar as calendar_source
from cos.sources import chat as chat_source
from cos.sources import mail as mail_source
from cos.sources.window import Window

log = get_logger("sources.collect")


@dataclass
class SourceBundle:
    """Everything one run retrieved, normalised."""

    window: Window
    mail: list[MailMessage] = field(default_factory=list)
    chat: list[ChatMessage] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.mail) + len(self.chat) + len(self.events)


def collect(
    client: GraphClient,
    window: Window,
    *,
    sources: list[str],
    operator_address: str | None = None,
    recorder: RunRecorder | None = None,
    chat_enabled: bool = False,
) -> SourceBundle:
    bundle = SourceBundle(window=window)

    if recorder is not None:
        recorder.sources_requested = list(sources)

    for name in sources:
        try:
            if name == "mail":
                bundle.mail = mail_source.fetch(client, window, operator_address=operator_address)
            elif name == "calendar":
                bundle.events = calendar_source.fetch(client, window)
            elif name == "chat":
                # `chat_source.fetch` is implemented and correct. It is not reachable
                # here because a consumer Microsoft account does not expose Teams chat
                # through Graph, and an application identity needs protected-API
                # approval. Attempting it produces a 401 that reads like a bug; failing
                # deliberately produces a brief that says chat is missing.
                #
                # Set `chat_enabled: true` in settings once pointed at a work tenant with
                # delegated auth. See research.md R-012 and docs/decisions.md B-001.
                if not chat_enabled:
                    raise GraphError(501, "/chats", "Teams chat is unavailable for this account")
                bundle.chat = chat_source.fetch(client, window, operator_address=operator_address)
            else:
                raise ValueError(f"unknown source: {name}")
        except (GraphError, ValueError) as exc:
            bundle.failed.append(name)
            log.warning("source unavailable", source=name, error=str(exc)[:200])
            if recorder is not None:
                recorder.source_result(name, ok=False)
            continue

        if recorder is not None:
            recorder.source_result(name, ok=True)

    if recorder is not None:
        recorder.count("messages_in", bundle.total)

    return bundle
