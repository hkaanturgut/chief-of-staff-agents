"""Pending proposals, shaped for the console.

Reads the files on disk rather than anything cached in memory, for the same reason the
executor does: a human may have edited a draft, and the console must show what would
actually be sent, not what was originally proposed.
"""

from __future__ import annotations

from typing import Any

from cos.outbox import writer
from cos.outbox.reader import ProposalError, parse

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def pending() -> dict[str, Any]:
    """Every proposal awaiting a decision, plus anything unreadable.

    Parsed one file at a time rather than through `load_directory`, which raises on the
    first bad file. On stage, one malformed draft blanking the whole queue would be the
    worst possible failure — so a broken file is reported next to the ones that loaded,
    not instead of them.
    """
    items: list[dict[str, Any]] = []
    broken: list[dict[str, str]] = []

    directory = writer.PENDING
    if not directory.exists():
        return {"items": [], "broken": [], "count": 0}

    for path in sorted(directory.glob("*.md")):
        try:
            action, meta = parse(path)
        except ProposalError as exc:
            broken.append({"file": path.name, "error": str(exc)})
            continue

        target = action.target.model_dump(mode="json")
        subject = str(target.get("subject") or target.get("title") or "")
        recipients = target.get("to") or target.get("attendees") or []
        if isinstance(recipients, str):
            recipients = [recipients]

        items.append(
            {
                "file": path.name,
                "action_id": action.id,
                "todo_id": action.todo_id,
                "kind": action.kind,
                "risk": action.risk,
                "subject": subject or action.rationale[:80],
                "recipients": list(recipients),
                "rationale": action.rationale,
                "body": action.body_markdown,
                "sources": len(action.sources),
                "run_id": str(meta.get("run_id") or ""),
                # The drafter marks anything it could not resolve. Surfacing it here is
                # the point of the console: it is exactly what a reviewer must look at.
                "needs_you": "NEEDS YOU BEFORE SENDING" in (action.body_markdown or ""),
            }
        )

    items.sort(key=lambda i: (RISK_ORDER.get(str(i["risk"]), 3), str(i["file"])))
    return {"items": items, "broken": broken, "count": len(items)}
