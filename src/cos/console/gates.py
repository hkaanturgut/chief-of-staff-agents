"""The two gates, read and operated through the operator's own credential.

This module is the reason the console is safe to put a button on.

`cos propose` opens a pull request. `execute.yml` declares `environment: send`, and the
Azure OIDC token that permits a send is issued by GitHub **only after** a reviewer
approves that environment. So the gate is not a policy the agent is asked to respect —
it is a credential the agent has no way to obtain.

Everything here shells out to `gh`, which authenticates as the human running the console.
The console therefore has exactly the authority its operator already had, and no more. If
`gh` is not installed or not signed in, the button reports that and does nothing. It never
falls back to a token of its own, because a token of its own is the thing that would make
this a bypass rather than an interface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from cos.logging import get_logger

log = get_logger("console.gates")

TIMEOUT_S = 20

# The workflow whose environment approval is gate 2.
EXECUTE_WORKFLOW = "execute.yml"


class GateError(RuntimeError):
    """A gate could not be read or operated. Always surfaced, never swallowed."""


def _gh(*args: str) -> str:
    if shutil.which("gh") is None:
        raise GateError("The GitHub CLI (`gh`) is not installed, so the gates cannot be read.")
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"`gh {args[0]}` timed out after {TIMEOUT_S}s.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise GateError(detail[-1] if detail else f"`gh {' '.join(args)}` failed.")
    return proc.stdout


def _gh_json(*args: str) -> Any:
    raw = _gh(*args).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateError("Unreadable response from `gh`.") from exc


def whoami() -> str | None:
    try:
        return str(_gh("api", "user", "-q", ".login")).strip() or None
    except GateError:
        return None


def open_pulls(repo: str) -> list[dict[str, Any]]:
    """Pull requests this pipeline opened and a human has not yet merged — gate 1."""
    data = _gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        "number,title,headRefName,url,isDraft,mergeable",
    )
    return list(data or [])


def waiting_runs(repo: str) -> list[dict[str, Any]]:
    """`execute` runs halted at the protected environment — gate 2.

    `waiting` is the state GitHub reports while a required reviewer has not yet acted. It
    is the only state in which approving does anything, so it is the only one the console
    offers a button for.
    """
    data = _gh_json(
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        EXECUTE_WORKFLOW,
        "--limit",
        "10",
        "--json",
        "databaseId,status,conclusion,displayTitle,url,createdAt",
    )
    return [r for r in (data or []) if r.get("status") == "waiting"]


def pending_environments(repo: str, run_db_id: int) -> list[dict[str, Any]]:
    """The environments a given run is blocked on, and whether *this* user may approve."""
    data = _gh_json("api", f"repos/{repo}/actions/runs/{run_db_id}/pending_deployments")
    out = []
    for entry in data or []:
        env = entry.get("environment") or {}
        out.append(
            {
                "environment_id": env.get("id"),
                "environment": env.get("name"),
                "wait_timer": entry.get("wait_timer"),
                "current_user_can_approve": bool(entry.get("current_user_can_approve")),
            }
        )
    return out


def state(repo: str) -> dict[str, Any]:
    """Both gates as the console shows them. Errors are data, not exceptions."""
    result: dict[str, Any] = {
        "repo": repo,
        "user": None,
        "pulls": [],
        "waiting": [],
        "error": None,
    }
    try:
        result["user"] = whoami()
        result["pulls"] = open_pulls(repo)
        waiting = waiting_runs(repo)
        for run in waiting:
            run["environments"] = pending_environments(repo, int(run["databaseId"]))
        result["waiting"] = waiting
    except GateError as exc:
        result["error"] = str(exc)
    return result


def approve(repo: str, run_db_id: int, environment_id: int, comment: str) -> dict[str, Any]:
    """Approve one pending environment, as the human running the console.

    This is the same call github.com makes when the reviewer clicks Approve. It succeeds
    only because `gh` is signed in as somebody GitHub already accepts as a reviewer for
    that environment — which is the whole point. There is no path here that works when
    the operator is not entitled to approve.
    """
    _gh(
        "api",
        "--method",
        "POST",
        f"repos/{repo}/actions/runs/{run_db_id}/pending_deployments",
        "-f",
        f"environment_ids[]={environment_id}",
        "-f",
        "state=approved",
        "-f",
        f"comment={comment}",
    )
    log.info("environment approved", repo=repo, run=run_db_id, environment=environment_id)
    return {"approved": True, "run": run_db_id, "environment_id": environment_id}


def merge_pull(repo: str, number: int) -> dict[str, Any]:
    """Merge one pull request — gate 1, again as the operator."""
    _gh("pr", "merge", str(number), "--repo", repo, "--merge")
    log.info("pull request merged", repo=repo, pr=number)
    return {"merged": True, "number": number}
