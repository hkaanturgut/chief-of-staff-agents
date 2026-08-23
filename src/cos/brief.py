"""Rendering BRIEF.md — the human-facing output.

This file is the product. The pull request body is this document, so it has to read like
something a person wrote for another person: ranked, sourced, and honest about what it
could not see.
"""

from __future__ import annotations

from cos.models import RunManifest, TodoItem
from cos.settings import REPO_ROOT, StalenessSettings

BRIEF_PATH = REPO_ROOT / "BRIEF.md"

OWNER_LABEL = {"me": "you", "delegate": "delegate", "waiting": "waiting on them"}
ACTION_LABEL = {
    "reply": "reply",
    "schedule": "schedule",
    "delegate": "delegate",
    "create_issue": "open an issue",
    "no_action": "no action",
}


def _urgency_band(value: int) -> str:
    if value >= 70:
        return "🔴"
    if value >= 40:
        return "🟠"
    return "⚪️"


def render_manifest(manifest: RunManifest) -> str:
    lines = [
        f"**Window** {manifest.window_start:%a %d %b %H:%M} → "
        f"{manifest.window_end:%a %d %b %H:%M}  ·  "
        f"**{manifest.messages_in}** items in  ·  "
        f"**{manifest.signals}** signals  ·  "
        f"**{manifest.clusters}** clusters  ·  "
        f"**{manifest.todos}** to-dos"
        + (
            f"  ·  **{manifest.dismissed}** dismissed as needing nothing"
            if manifest.dismissed
            else ""
        ),
        "",
        f"**{manifest.model_calls}** model calls  ·  "
        f"{manifest.input_tokens:,} in / {manifest.output_tokens:,} out tokens  ·  "
        f"~${manifest.estimated_cost_usd:.3f}  ·  "
        f"{manifest.wall_seconds:.1f}s wall",
    ]
    if manifest.sources_failed:
        # Never silent. A reader must be able to tell "nothing there" from "did not look".
        missing = ", ".join(manifest.sources_failed)
        lines += [
            "",
            f"> ⚠️ **Source unavailable: {missing}.** This brief does not cover "
            f"{missing}, so anything that arrived only through {missing} is missing "
            "from it.",
        ]
    if manifest.dry_run:
        lines += ["", "> 🧪 Dry run — nothing can be sent from this run."]
    return "\n".join(lines)


def render_todo(todo: TodoItem, staleness: StalenessSettings) -> str:
    due = f"**due {todo.due:%a %d %b}**" if todo.due else "no stated deadline"
    lines = [
        f"### {_urgency_band(todo.urgency)} {todo.title}",
        "",
        f"`{todo.urgency}` · {due} · {OWNER_LABEL.get(todo.owner, todo.owner)} · "
        f"{ACTION_LABEL.get(todo.suggested_action, todo.suggested_action)}",
        "",
    ]
    if todo.detail and todo.detail != todo.title:
        lines += [todo.detail, ""]
    lines += [f"*Why this rank:* {todo.urgency_reason}", ""]

    if todo.needs_human_judgment:
        lines += [
            "> 🤔 **Needs your judgement.** No action is proposed for this — the system "
            "does not understand it well enough to draft one.",
            "",
        ]
    if todo.seen_in_runs >= staleness.flag_after_runs:
        lines += [
            f"> ⏳ **Stale.** This has appeared in {todo.seen_in_runs} consecutive runs "
            "without being actioned or dismissed.",
            "",
        ]

    lines.append(f"<details><summary>{len(todo.sources)} source(s)</summary>")
    lines.append("")
    for source in todo.sources:
        stamp = f"{source.timestamp:%a %d %b %H:%M}"
        label = f"{source.kind} · {source.author} · {stamp}"
        link = f"[{label}]({source.permalink})" if source.permalink else label
        lines.append(f"- {link}<br>{source.excerpt}")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def render(todos: list[TodoItem], manifest: RunManifest, staleness: StalenessSettings) -> str:
    header = [
        "# Chief of Staff brief",
        "",
        render_manifest(manifest),
        "",
        "---",
        "",
    ]

    if not todos:
        header += [
            "Nothing needs you in this window.",
            "",
            "That is a result, not a failure — but check the source note above before trusting it.",
            "",
        ]
        return "\n".join(header)

    actionable = [t for t in todos if t.suggested_action != "no_action"]
    header += [
        f"**{len(todos)}** to-dos, **{len(actionable)}** with a proposed action.",
        "",
    ]

    body = [render_todo(todo, staleness) for todo in todos]
    return "\n".join([*header, *body])


def write(todos: list[TodoItem], manifest: RunManifest, staleness: StalenessSettings) -> str:
    content = render(todos, manifest, staleness)
    BRIEF_PATH.write_text(content)
    return content
