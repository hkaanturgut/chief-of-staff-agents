"""`cos` — the operator's entry point.

Every command here traverses the same code as the workflows do. In particular `execute`
runs the same executor, with the same allowlist, dry-run, ledger, and per-run maximum
checks. A control implemented in workflow YAML is a control a local run bypasses, so
none of them are.
"""

from __future__ import annotations

from typing import Annotated

import typer

from cos.logging import configure, get_logger

app = typer.Typer(
    name="cos",
    help="Chief of Staff. The agents propose, git decides, the human merges.",
    no_args_is_help=True,
    add_completion=False,
)
log = get_logger("cli")

NOT_BUILT = "not implemented yet"


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
    json_logs: Annotated[bool, typer.Option("--json", help="JSON log output.")] = False,
) -> None:
    configure(verbose=verbose, json_output=json_logs)


@app.command()
def login() -> None:
    """Authenticate to Microsoft Graph by device code, and cache the token.

    Run this within 30 minutes of any demonstration. An expired token is the single most
    likely way a live run fails.
    """
    from cos.graph.auth import GraphAuthError, from_settings

    auth = from_settings()

    existing = auth.signed_in_account()
    if existing and auth.acquire_silent():
        typer.echo(f"Already signed in as {existing}. Token is valid.")
        return

    try:
        auth.login(prompt=lambda message: typer.echo(message))
    except GraphAuthError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Signed in as {auth.signed_in_account()}.")


@app.command()
def provision() -> None:
    """Provision agents/*.yaml into the Foundry project. Idempotent."""
    raise typer.Exit(_todo("provision", "T037"))


@app.command()
def brief(
    raw: Annotated[
        bool, typer.Option("--raw", help="Print normalised source objects only.")
    ] = False,
    signals: Annotated[
        bool, typer.Option("--signals", help="Print extracted signals only.")
    ] = False,
    hours: Annotated[
        int | None, typer.Option("--hours", help="Override the lookback window.")
    ] = None,
) -> None:
    """Produce the ranked, deduplicated brief. Writes BRIEF.md. Sends nothing."""
    raise typer.Exit(_todo("brief", "T028, T039, T049"))


@app.command()
def propose() -> None:
    """Draft the actions, write outbox/pending/, and open a pull request. Sends nothing."""
    raise typer.Exit(_todo("propose", "T061"))


@app.command()
def execute(
    yes: Annotated[bool, typer.Option("--yes", help="Skip the local confirmation prompt.")] = False,
) -> None:
    """Perform approved actions. Honours dry-run, the allowlist, the ledger, and the cap."""
    raise typer.Exit(_todo("execute", "T071"))


@app.command()
def replay(run_id: Annotated[str, typer.Argument(help="A run id under state/runs/.")]) -> None:
    """Re-render a past run's brief and manifest from its recorded log."""
    raise typer.Exit(_todo("replay", "T015"))


def _todo(command: str, tasks: str) -> int:
    log.error("command not implemented", command=command, tasks=tasks)
    typer.echo(f"`cos {command}` is {NOT_BUILT} — see tasks {tasks}.", err=True)
    return 1


if __name__ == "__main__":
    app()
