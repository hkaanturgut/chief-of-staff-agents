"""The demo corpus.

41 normalised items carrying all six planted traps, in `demo/corpus.json`. It exists so
the whole pipeline can be run, demonstrated, and evaluated with no mailbox at all —
which is what makes the consolidator evaluation possible in CI, and what makes a
rehearsal possible on a train.

Normalised rather than raw Graph payloads on purpose: the Graph-to-model layer has its
own tests, and keeping the corpus at the model level makes the traps readable in a diff.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from cos.models import CalendarEvent, ChatMessage, MailMessage
from cos.settings import REPO_ROOT
from cos.sources.collect import SourceBundle
from cos.sources.window import Window

CORPUS_PATH = REPO_ROOT / "demo" / "corpus.json"


def load(path: Path | None = None) -> tuple[SourceBundle, datetime, str]:
    """Return the bundle, the corpus's own "now", and the operator address."""
    payload = json.loads((path or CORPUS_PATH).read_text())
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
    return (
        SourceBundle(window=window, mail=mail, chat=chat, events=events),
        now,
        str(payload["operator"]),
    )
