"""Reading proposals back from disk.

The executor reads the file's CURRENT contents, not what was proposed. A human may have
edited the body, the recipient, or both, and the allowlist has to be evaluated against
what is actually there (FR-033).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter

from cos.models import (
    ChatTarget,
    EventTarget,
    IssueTarget,
    MailTarget,
    ProposedAction,
    SourceRef,
)

TARGET_TYPES: dict[str, type[Any]] = {
    "send_mail": MailTarget,
    "reply_mail": MailTarget,
    "create_event": EventTarget,
    "post_chat": ChatTarget,
    "create_issue": IssueTarget,
}


class ProposalError(ValueError):
    """A proposal file that cannot be trusted enough to act on."""


def parse(path: Path) -> tuple[ProposedAction, dict[str, Any]]:
    """Return the action and its full frontmatter.

    The body is taken from below the frontmatter rather than from any cached copy, which
    is what makes a human edit authoritative.
    """
    try:
        document = frontmatter.loads(path.read_text())
    except Exception as exc:
        raise ProposalError(f"{path.name}: cannot parse frontmatter: {exc}") from exc

    meta: dict[str, Any] = dict(document.metadata)
    kind = str(meta.get("kind", ""))
    if kind not in TARGET_TYPES:
        raise ProposalError(f"{path.name}: unknown kind {kind!r}")

    body = document.content
    # Strip the editing hint if the human left it in place.
    if body.lstrip().startswith("<!--"):
        _, _, rest = body.partition("-->")
        body = rest or body

    try:
        action = ProposedAction(
            id=str(meta["id"]),
            todo_id=str(meta["todo_id"]),
            kind=kind,  # type: ignore[arg-type]
            risk=str(meta.get("risk", "high")),  # type: ignore[arg-type]
            target=TARGET_TYPES[kind].model_validate(meta.get("target") or {}),
            body_markdown=body.strip(),
            rationale=str(meta.get("rationale") or "no rationale recorded"),
            sources=[SourceRef.model_validate(s) for s in (meta.get("sources") or [])],
        )
    except Exception as exc:
        raise ProposalError(f"{path.name}: {exc}") from exc

    return action, meta


def load_directory(directory: Path) -> list[tuple[Path, ProposedAction, dict[str, Any]]]:
    results = []
    for path in sorted(directory.glob("*.md")):
        action, meta = parse(path)
        results.append((path, action, meta))
    return results
