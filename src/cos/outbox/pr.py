"""Branch, commit, and open the pull request.

The pull request is the review surface and the first of the two gates. Its body is the
brief plus a risk-labelled table, so a reviewer sees the reasoning and the proposed acts
in the same place they approve them.

Git and the GitHub CLI are shelled out to rather than wrapped in a library. It keeps the
dependency surface small, and every command here is one a human could run by hand — which
matters when something goes wrong in front of an audience.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cos.logging import get_logger
from cos.models import ProposedAction, RunManifest
from cos.settings import REPO_ROOT, GitHubSettings

log = get_logger("outbox.pr")

RISK_MARK = {"low": "🟢 low", "medium": "🟠 medium", "high": "🔴 **high**"}
KIND_LABEL = {
    "send_mail": "send mail",
    "reply_mail": "reply",
    "create_event": "create event",
    "post_chat": "post to chat",
    "create_issue": "open issue",
}


class GitError(RuntimeError):
    pass


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd or REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitError(f"{' '.join(args)} failed: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


@dataclass(frozen=True)
class PullRequest:
    branch: str
    url: str
    number: int | None


def branch_name(settings: GitHubSettings, when: datetime) -> str:
    return f"{settings.branch_prefix}{when:%Y%m%d-%H%M}"


def action_table(actions: list[ProposedAction]) -> str:
    if not actions:
        return "_No actions proposed._"
    lines = [
        "| | Action | Risk | To | Why |",
        "|---|---|---|---|---|",
    ]
    for action in actions:
        target = action.target.model_dump(mode="json")
        recipients = ", ".join(target.get("to", []) or target.get("attendees", []) or [])
        if not recipients:
            recipients = str(target.get("chat_id") or target.get("repo") or "—")
        rationale = action.rationale.replace("\n", " ")[:120]
        lines.append(
            f"| ☐ | {KIND_LABEL.get(action.kind, action.kind)} "
            f"| {RISK_MARK.get(action.risk, action.risk)} | `{recipients}` | {rationale} |"
        )
    return "\n".join(lines)


def body(brief: str, actions: list[ProposedAction], skipped: int) -> str:
    parts = [
        "## Proposed actions",
        "",
        action_table(actions),
        "",
        "Each row is a file in `outbox/pending/`. **Edit any draft directly in the diff** "
        "— the body below the frontmatter is what gets sent.",
        "",
    ]
    if skipped:
        parts += [
            f"> ⚠️ **Stopped at the per-run cap.** {skipped} further actionable to-do(s) "
            "were not drafted. Raise `max_actions_per_run` in `config/settings.yaml` if "
            "that is wrong.",
            "",
        ]
    parts += [
        "Merging this stages the actions. **Nothing is sent until the `send` environment "
        "is approved** — that is the second gate.",
        "",
        "---",
        "",
        brief,
    ]
    return "\n".join(parts)


def title(manifest: RunManifest, actions: list[ProposedAction]) -> str:
    return (
        f"Chief of Staff: {manifest.todos} to-dos, {len(actions)} proposed actions "
        f"({manifest.window_end:%Y-%m-%d})"
    )


def open_pull_request(
    *,
    settings: GitHubSettings,
    manifest: RunManifest,
    actions: list[ProposedAction],
    brief: str,
    skipped: int,
    paths: list[Path],
    when: datetime,
    reviewer: str | None = None,
) -> PullRequest:
    branch = branch_name(settings, when)
    base = run("git", "rev-parse", "--abbrev-ref", "HEAD")

    run("git", "checkout", "-b", branch)
    try:
        for path in [*paths, REPO_ROOT / "BRIEF.md"]:
            run("git", "add", str(path))
        run(
            "git",
            "-c",
            "user.name=Chief of Staff",
            "-c",
            "user.email=chief-of-staff@users.noreply.github.com",
            "commit",
            "-m",
            title(manifest, actions),
        )
        run("git", "push", "-u", "origin", branch)

        args = [
            "gh",
            "pr",
            "create",
            "--repo",
            settings.repo,
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title(manifest, actions),
            "--body",
            body(brief, actions, skipped),
        ]
        if any(a.risk == "high" for a in actions):
            args += ["--label", settings.high_risk_label]
        if reviewer:
            args += ["--reviewer", reviewer]
        url = run(*args).splitlines()[-1]
    finally:
        run("git", "checkout", base)

    number = None
    if "/pull/" in url:
        try:
            number = int(url.rsplit("/", 1)[1])
        except ValueError:
            number = None

    log.info("pull request opened", url=url, actions=len(actions))
    return PullRequest(branch=branch, url=url, number=number)
