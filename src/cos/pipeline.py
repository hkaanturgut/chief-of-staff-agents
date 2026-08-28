"""The pipeline: sources in, ranked to-dos out.

Fixed order, called from code. Which *sources* to pull is a routing decision a model can
usefully make; consolidate-then-draft never varies, so it is a function call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from azure.identity.aio import AzureCliCredential

from cos.agents import definitions, extract
from cos.agents.runner import AgentRunner
from cos.consolidate.merge import consolidate
from cos.consolidate.prepass import build_clusters
from cos.graph.auth import GraphAuth
from cos.graph.client import GraphClient
from cos.logging import get_logger
from cos.manifest import RunRecorder, new_run_id
from cos.models import RunManifest, Signal, TodoItem
from cos.settings import (
    Environment,
    ImportantSenders,
    Settings,
)
from cos.sources.collect import SourceBundle, collect
from cos.sources.window import Window, resolve

log = get_logger("pipeline")


@dataclass
class BriefResult:
    manifest: RunManifest
    bundle: SourceBundle
    signals: list[Signal] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)


def operator_label(env: Environment, override: str | None = None) -> str:
    return override or env.graph_mailbox or "the operator"


async def run_brief(
    *,
    settings: Settings,
    env: Environment,
    senders: ImportantSenders,
    auth: GraphAuth | None = None,
    lookback_hours: int | None = None,
    now: datetime | None = None,
    stop_after: str | None = None,
    bundle_override: SourceBundle | None = None,
    operator_override: str | None = None,
) -> BriefResult:
    """Retrieve, extract, consolidate.

    `stop_after` is "sources" or "signals" for the intermediate CLI views. It exists so
    the demo can show each layer separately, and so a failure can be located to a stage.
    """
    now = now or datetime.now(UTC)
    window: Window = resolve(settings.window, now=now, lookback_hours=lookback_hours)
    recorder = RunRecorder(
        # Wall clock, deliberately not `now`. Under `--corpus`, `now` is the corpus's own
        # frozen timestamp, so deriving the run id from it gave every corpus run the same
        # id — and every rehearsal appended to one log file. The console then showed five
        # runs added together: 147 calls, a wall clock shorter than one of them, and a
        # cost five times the real figure. A run is identified by when it ran; the corpus
        # only supplies what the data thinks "now" is.
        run_id=new_run_id(),
        window_start=window.start,
        window_end=window.end,
        dry_run=settings.run.dry_run,
    )

    if bundle_override is not None:
        # The corpus path. Live Graph is bypassed, everything above it is identical —
        # which is what makes the offline evaluation meaningful rather than a mock of
        # itself.
        bundle = bundle_override
        recorder.sources_requested = ["mail", "chat", "calendar"]
        for name in recorder.sources_requested:
            recorder.source_result(name, ok=True)
        recorder.count("messages_in", bundle.total)
    else:
        assert auth is not None, "a live run needs Graph auth"
        with GraphClient(auth=auth) as client:
            bundle = collect(
                client,
                window,
                sources=settings.sources.attended,
                operator_address=env.graph_mailbox,
                recorder=recorder,
            )

    if stop_after == "sources":
        return BriefResult(manifest=recorder.finish(), bundle=bundle)

    env.require("foundry_project_endpoint", "azure_tenant_id")
    assert env.foundry_project_endpoint and env.azure_tenant_id

    agents = definitions.load_all()
    models = settings.models

    async with (
        AzureCliCredential(tenant_id=env.azure_tenant_id) as credential,
        AgentRunner(
            project_endpoint=env.foundry_project_endpoint,
            credential=credential,
            recorder=recorder,
        ) as runner,
    ):
        # The three ingest agents run concurrently. They are independent by design —
        # that independence is the reason they are separate agents at all.
        tz = ZoneInfo(settings.window.timezone)
        mail_signals, chat_signals, event_signals = await asyncio.gather(
            extract.extract_mail(runner, agents["mail-triage"], models, bundle.mail, tz),
            extract.extract_chat(runner, agents["chat-triage"], models, bundle.chat, tz),
            extract.extract_calendar(runner, agents["calendar-context"], models, bundle.events, tz),
        )
        signals = [*mail_signals, *chat_signals, *event_signals]
        recorder.count("signals", len(signals))
        log.info(
            "signals extracted",
            mail=len(mail_signals),
            chat=len(chat_signals),
            calendar=len(event_signals),
        )

        if stop_after == "signals":
            return BriefResult(manifest=recorder.finish(), bundle=bundle, signals=signals)

        # fyi means "read it, nothing to do". Those are not to-dos, and letting them
        # through has three costs: a model call per cluster, a brief that reads like an
        # inbox summary, and — worst — a bundle of twelve inert notices outranking real
        # work, because urgency counts distinct sources and twelve of them saturates it.
        #
        # The count is kept and reported, so the brief can say what it dismissed rather
        # than pretending it never saw it.
        actionable = [s for s in signals if s.type != "fyi"]
        recorder.count("dismissed", len(signals) - len(actionable))
        log.info("inert signals dismissed", dismissed=len(signals) - len(actionable))

        subject_lookup = {m.id: m.subject for m in bundle.mail}
        clusters = build_clusters(actionable, subject_lookup=subject_lookup)
        recorder.count("clusters", len(clusters))
        log.info("clusters built", signals=len(signals), clusters=len(clusters))

        todos = await consolidate(
            runner,
            agents["consolidator"].model_tier(models),
            actionable,
            clusters,
            now=now,
            urgency_settings=settings.urgency,
            senders=senders,
            instructions=agents["consolidator"].instructions,
            operator=operator_label(env, operator_override),
        )

    recorder.count("todos", len(todos))
    return BriefResult(manifest=recorder.finish(), bundle=bundle, signals=signals, todos=todos)
