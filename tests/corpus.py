"""Loading the demo corpus.

`tests/golden/corpus.json` holds 41 normalised items carrying all six planted traps. It
is the input to the consolidator evaluation, and it is what `cos brief --corpus` runs
against when no live mailbox is available.

Normalised rather than raw Graph payloads on purpose: the Graph-to-model layer has its
own tests, and keeping the corpus at the model level makes the traps readable in a diff.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from cos.models import CalendarEvent, ChatMessage, MailMessage
from cos.sources.collect import SourceBundle
from cos.sources.window import Window

CORPUS_PATH = Path(__file__).parent / "golden" / "corpus.json"


def load() -> tuple[SourceBundle, datetime, str]:
    payload = json.loads(CORPUS_PATH.read_text())
    now = datetime.fromisoformat(payload["now"])

    mail = [MailMessage.model_validate(m) for m in payload["mail"]]
    chat = [ChatMessage.model_validate(c) for c in payload["chat"]]
    events = [CalendarEvent.model_validate(e) for e in payload["events"]]

    starts = [m.received_at for m in mail] + [c.sent_at for c in chat]
    window = Window(
        start=min(starts),
        end=now,
        calendar_start=min(e.start for e in events),
        calendar_end=max(e.end for e in events),
    )
    bundle = SourceBundle(window=window, mail=mail, chat=chat, events=events)
    return bundle, now, str(payload["operator"])
