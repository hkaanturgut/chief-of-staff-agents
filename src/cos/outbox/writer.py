"""Writing proposals to disk.

One file per action at `outbox/pending/<action_id>.md`: YAML frontmatter for the machine,
markdown body for the person. The body below the frontmatter is what gets sent — so a
human editing it in the GitHub web editor is authoritative by construction, which is the
whole reason the review surface is a diff.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from cos.models import ProposedAction
from cos.settings import REPO_ROOT

OUTBOX = REPO_ROOT / "outbox"
PENDING = OUTBOX / "pending"
SENT = OUTBOX / "sent"
FAILED = OUTBOX / "failed"

BODY_MARKER = (
    "<!-- Everything below the frontmatter is the message body. Edit it freely;\n"
    "     your edit is what gets sent. -->"
)


def frontmatter(
    action: ProposedAction,
    *,
    status: str = "pending",
    run_id: str = "",
    model: str = "",
    model_version: str = "",
    generated_at: datetime | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": action.id,
        "todo_id": action.todo_id,
        "kind": action.kind,
        "risk": action.risk,
        "status": status,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "run_id": run_id,
        "model": model,
        "model_version": model_version,
        "target": action.target.model_dump(mode="json"),
        "rationale": action.rationale,
        "sources": [s.model_dump(mode="json") for s in action.sources],
    }
    if extra:
        payload.update(extra)
    return payload


def render(action: ProposedAction, **kwargs: object) -> str:
    header = yaml.safe_dump(
        frontmatter(action, **kwargs),  # type: ignore[arg-type]
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{header}---\n\n{BODY_MARKER}\n\n{action.body_markdown.strip()}\n"


def write(action: ProposedAction, *, directory: Path | None = None, **kwargs: object) -> Path:
    target = directory or PENDING
    target.mkdir(parents=True, exist_ok=True)
    # The filename is the idempotency key, so the queue is self-indexing and a duplicate
    # is a filesystem collision rather than a logic error discovered after sending.
    path = target / f"{action.id}.md"
    path.write_text(render(action, **kwargs))
    return path


def write_all(
    actions: list[ProposedAction], *, directory: Path | None = None, **kwargs: object
) -> list[Path]:
    return [write(action, directory=directory, **kwargs) for action in actions]
