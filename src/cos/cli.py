"""`cos` — the operator's entry point.

Every command here traverses the same code as the workflows do. In particular `execute`
runs the same executor, with the same allowlist, dry-run, ledger, and per-run maximum
checks. A control implemented in workflow YAML is a control a local run bypasses, so
none of them are.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    import asyncio

    from cos import brief as brief_doc
    from cos.graph.auth import GraphAuthError, from_settings
    from cos.pipeline import run_brief
    from cos.settings import load_environment, load_important_senders, load_settings

    settings = load_settings()
    env = load_environment()

    try:
        auth = from_settings()
        auth.token()
    except GraphAuthError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    stop_after = "sources" if raw else ("signals" if signals else None)

    result = asyncio.run(
        run_brief(
            settings=settings,
            env=env,
            senders=load_important_senders(),
            auth=auth,
            lookback_hours=hours,
            stop_after=stop_after,
        )
    )

    if raw:
        _print_raw(result.bundle)
        return

    if signals:
        _print_signals(result.signals)
        return

    content = brief_doc.write(result.todos, result.manifest, settings.staleness)
    typer.echo(content)
    typer.echo(f"\nWritten to {brief_doc.BRIEF_PATH.name}", err=True)


def _print_raw(bundle: object) -> None:
    """Normalised source objects, for proving retrieval end to end."""
    from cos.sources.collect import SourceBundle

    assert isinstance(bundle, SourceBundle)
    w = bundle.window
    typer.echo(
        f"window {w.start:%a %d %b %H:%M} -> {w.end:%a %d %b %H:%M} "
        f"({w.hours:.0f}h)  |  calendar {w.calendar_start:%d %b} -> {w.calendar_end:%d %b}"
    )
    if bundle.failed:
        typer.echo(f"sources unavailable: {', '.join(bundle.failed)}")
    typer.echo("")

    typer.echo(f"MAIL ({len(bundle.mail)})")
    for m in bundle.mail:
        who = "you" if m.is_from_operator else (m.from_name or m.from_address)
        typer.echo(f"  {m.received_at:%d %b %H:%M}  {who[:24]:24}  {m.subject[:60]}")

    typer.echo(f"\nCALENDAR ({len(bundle.events)})")
    for e in bundle.events:
        typer.echo(f"  {e.start:%d %b %H:%M}-{e.end:%H:%M}  {e.subject[:60]}")

    typer.echo(f"\nCHAT ({len(bundle.chat)})")
    for c in bundle.chat:
        typer.echo(f"  {c.sent_at:%d %b %H:%M}  {(c.from_name or '?')[:24]:24}  {c.body_text[:60]}")


def _print_signals(signals: Sequence[object]) -> None:
    """Extracted signals with their provenance, before any deduplication."""
    from cos.models import Signal

    typer.echo(f"SIGNALS ({len(signals)}) — before consolidation\n")
    for signal in signals:
        assert isinstance(signal, Signal)
        due = f"due {signal.due:%a %d %b}" if signal.due else "no deadline"
        flag = " [ambiguous]" if signal.ambiguous else ""
        typer.echo(f"  {signal.type:12} {due:18}{flag}")
        typer.echo(f"    {signal.statement}")
        for source in signal.sources:
            typer.echo(
                f"      <- {source.kind}:{source.id[:18]} {source.author[:22]} "
                f"{source.timestamp:%d %b %H:%M}"
            )
        typer.echo("")


@app.command()
def propose(
    hours: Annotated[
        int | None, typer.Option("--hours", help="Override the lookback window.")
    ] = None,
    no_pr: Annotated[
        bool, typer.Option("--no-pr", help="Write proposals but do not open a PR.")
    ] = False,
    corpus: Annotated[
        bool, typer.Option("--corpus", help="Run against the committed demo corpus.")
    ] = False,
) -> None:
    """Draft the actions, write outbox/pending/, and open a pull request. Sends nothing."""
    import asyncio
    from datetime import UTC, datetime

    from azure.identity.aio import AzureCliCredential

    from cos import brief as brief_doc
    from cos.agents import definitions
    from cos.agents.runner import AgentRunner
    from cos.draft.drafter import draft_all
    from cos.manifest import RunRecorder
    from cos.models import ProposedAction
    from cos.outbox import pr, writer
    from cos.pipeline import operator_label, run_brief
    from cos.settings import load_environment, load_important_senders, load_settings

    settings = load_settings()
    env = load_environment()
    now = datetime.now(UTC)

    bundle_override = None
    operator_override = None
    auth = None
    if corpus:
        from cos.corpus import load as load_corpus

        bundle_override, now, operator_override = load_corpus()
    else:
        from cos.graph.auth import GraphAuthError, from_settings

        try:
            auth = from_settings()
            auth.token()
        except GraphAuthError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    result = asyncio.run(
        run_brief(
            settings=settings,
            env=env,
            senders=load_important_senders(),
            auth=auth,
            lookback_hours=hours,
            now=now,
            bundle_override=bundle_override,
            operator_override=operator_override,
        )
    )

    brief_text = brief_doc.write(result.todos, result.manifest, settings.staleness)

    env.require("foundry_project_endpoint", "azure_tenant_id")
    endpoint = env.foundry_project_endpoint
    tenant = env.azure_tenant_id
    assert endpoint and tenant
    agents = definitions.load_all()
    recorder = RunRecorder(
        run_id=result.manifest.run_id,
        window_start=result.manifest.window_start,
        window_end=result.manifest.window_end,
        dry_run=settings.run.dry_run,
    )

    async def _draft() -> tuple[list[ProposedAction], int]:
        async with (
            AzureCliCredential(tenant_id=tenant) as credential,
            AgentRunner(
                project_endpoint=endpoint,
                credential=credential,
                recorder=recorder,
            ) as runner,
        ):
            return await draft_all(
                runner,
                agents["drafter"],
                agents["drafter"].model_tier(settings.models),
                result.todos,
                operator=operator_label(env, operator_override),
                repo=settings.github.repo,
                now=now,
                max_actions=settings.run.max_actions_per_run,
            )

    actions, skipped = asyncio.run(_draft())

    if not actions:
        # An empty pull request is noise (FR-027).
        typer.echo("No actions proposed. No pull request opened.")
        return

    paths = writer.write_all(
        actions,
        run_id=result.manifest.run_id,
        model=settings.models.strong.deployment,
        model_version=settings.models.strong.version,
        generated_at=now,
    )
    for path, action in zip(paths, actions, strict=True):
        typer.echo(f"  {action.risk:6} {action.kind:14} {path.name}")

    typer.echo(f"\n{len(actions)} proposal(s) written to outbox/pending/")

    if no_pr:
        typer.echo("--no-pr: stopping before the pull request.")
        return

    request = pr.open_pull_request(
        settings=settings.github,
        manifest=result.manifest,
        actions=actions,
        brief=brief_text,
        skipped=skipped,
        paths=paths,
        when=now,
    )
    typer.echo(f"\nPull request: {request.url}")
    typer.echo("Nothing is sent until it is merged AND the `send` environment approved.")


@app.command()
def execute(
    yes: Annotated[bool, typer.Option("--yes", help="Skip the local confirmation.")] = False,
) -> None:
    """Perform approved actions. Honours dry-run, the allowlist, the ledger, and the cap."""
    from cos.graph.auth import from_settings
    from cos.graph.client import GraphClient
    from cos.outbox import writer
    from cos.outbox.executor import Executor, execute_pending
    from cos.outbox.ledger import LedgerFile
    from cos.settings import load_allowed_recipients, load_settings

    settings = load_settings()
    allowed = load_allowed_recipients()

    pending = sorted(writer.PENDING.glob("*.md"))
    if not pending:
        typer.echo("Nothing pending.")
        return

    mode = "DRY RUN" if settings.run.dry_run else "LIVE — actions will really be performed"
    typer.echo(f"{len(pending)} pending action(s). Mode: {mode}")
    typer.echo(
        f"Allowlist: {len(allowed.addresses)} address(es), "
        f"{len(allowed.domains)} domain(s), {len(allowed.repos)} repo(s)"
    )

    if not settings.run.dry_run and not yes:
        typer.confirm("Perform these actions for real?", abort=True)

    executor = Executor(
        run=settings.run,
        allowed=allowed,
        ledger=LedgerFile(),
        client_factory=lambda: GraphClient(auth=from_settings()),
    )
    try:
        report = execute_pending(executor)
    finally:
        executor.close()

    for outcome in report.outcomes:
        typer.echo(f"  {outcome.status:8} {outcome.action_id}  {outcome.detail[:70]}")
    typer.echo(f"\n{len(report.sent)} performed, {len(report.failed)} failed.")
    if report.failed:
        raise typer.Exit(1)


@app.command()
def replay(run_id: Annotated[str, typer.Argument(help="A run id under state/runs/.")]) -> None:
    """Re-render a past run's brief and manifest from its recorded log."""
    raise typer.Exit(_todo("replay", "T015"))


@app.command()
def console(
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 7378,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Do not open a browser.")
    ] = False,
) -> None:
    """Open the operator console: the delegation view, the brief, and the two gates.

    The console reads runs from state/runs/ and drafts from outbox/pending/. Its approve
    button operates the protected `send` environment through your own `gh` credential —
    it holds no authority the person running it does not already have.
    """
    from cos.console.server import ConsoleBindError, serve
    from cos.settings import load_settings

    settings = load_settings()
    try:
        serve(
            repo=settings.github.repo,
            dry_run=settings.run.dry_run,
            port=port,
            open_browser=not no_browser,
        )
    except ConsoleBindError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _todo(command: str, tasks: str) -> int:
    log.error("command not implemented", command=command, tasks=tasks)
    typer.echo(f"`cos {command}` is {NOT_BUILT} — see tasks {tasks}.", err=True)
    return 1


if __name__ == "__main__":
    app()
