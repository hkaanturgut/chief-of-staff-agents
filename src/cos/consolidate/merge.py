"""Stage B: the LLM merge.

One call per candidate cluster produced by `prepass.py`. The model decides merge-or-split
within a cluster it was handed. It never discovers clusters, and it never sees the whole
signal set.

Everything mechanical is assembled here in code rather than asked of the model:

- `sources` is the union of the cluster's signals. A model asked to copy source lists will
  eventually drop one, and provenance is not best-effort (Constitution IV).
- `due` is carried forward from whichever signal explicitly stated it. Letting the model
  restate a date is how an inferred deadline gets in through the side door.
- `urgency` is arithmetic in `rank.py`. The model receives the score and its components so
  it can explain them, and its schema has no field to change them with.
- `id` is derived from content after the merge, so an unchanged inbox produces an
  unchanged diff.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from cos.agents.runner import AgentRunner, ModelTier
from cos.consolidate.prepass import Cluster
from cos.consolidate.rank import compute_urgency, earliest_explicit_due, explain_inputs
from cos.ids import todo_id
from cos.logging import get_logger
from cos.models import Signal, SourceRef, TodoItem
from cos.settings import ImportantSenders, UrgencySettings

log = get_logger("consolidate.merge")

AGENT = "consolidator"

VALID_OWNERS = {"me", "delegate", "waiting"}
VALID_ACTIONS = {"reply", "schedule", "delegate", "create_issue", "no_action"}


class ClusterMerge(BaseModel):
    """The consolidator's output for one candidate cluster.

    Note what is absent: urgency, due, sources, id. See contracts/README.md.
    """

    model_config = ConfigDict(extra="forbid")

    merge: bool = Field(description="True if every signal in this cluster is the same ask.")
    statement: str = Field(min_length=1, description="The merged ask, stated once.")
    title: str = Field(min_length=1, max_length=120)
    detail: str = ""
    owner: str = Field(description="One of: me, delegate, waiting.")
    suggested_action: str = Field(
        description="One of: reply, schedule, delegate, create_issue, no_action."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_judgment: bool = False
    urgency_reason: str = Field(
        description="One sentence explaining the urgency score you were given. "
        "You are not setting the score."
    )
    split_groups: list[list[int]] | None = Field(
        default=None,
        description="When merge is false, partition the input signal indices into the "
        "distinct asks they represent. Every index exactly once.",
    )


def render_cluster(
    signals: Sequence[Signal], urgency: Mapping[str, object], *, operator: str
) -> str:
    """The prompt body for one cluster. Compact, indexed, and fully attributed.

    `operator` is not decoration. Without it the model cannot tell `me` from `waiting`,
    and it reads the operator's own commitments as somebody else's — turning "I'll send
    the revised numbers by Friday" into "wait for Kaan to send the numbers".
    """
    lines = [
        f"You are briefing: {operator}",
        "Anything this person said they would do is a commitment they owe, owner=me.",
        "Anything addressed to them is an ask of them, owner=me.",
        "Use owner=waiting only when the operator is waiting on someone else to act.",
        "",
        "CANDIDATE CLUSTER",
        "",
    ]
    for index, signal in enumerate(signals):
        origin = ", ".join(f"{s.kind}:{s.author}" for s in signal.sources)
        lines.append(f"[{index}] type={signal.type} from=({origin})")
        lines.append(f"     statement: {signal.statement}")
        if signal.counterparty:
            lines.append(f"     counterparty: {signal.counterparty}")
        if signal.due:
            lines.append(f"     explicit deadline: {signal.due.isoformat()}")
        if signal.ambiguous:
            lines.append("     flagged ambiguous by the extracting agent")
        for source in signal.sources:
            lines.append(f"     - {source.kind} {source.timestamp:%d %b %H:%M}: {source.excerpt}")
        lines.append("")
    lines.append("URGENCY (already computed — explain it, do not change it)")
    lines.append(json.dumps(urgency, indent=2, default=str))
    return "\n".join(lines)


def _merged_sources(signals: Sequence[Signal]) -> list[SourceRef]:
    """The union of every source across the cluster, deduplicated and time-ordered.

    SourceRef hashes on (kind, id), so two signals citing one message cite it once.
    """
    seen: dict[tuple[str, str], SourceRef] = {}
    for signal in signals:
        for source in signal.sources:
            seen.setdefault((source.kind, source.id), source)
    return sorted(seen.values(), key=lambda s: s.timestamp)


def _coerce_owner(value: str) -> str:
    return value if value in VALID_OWNERS else "me"


def _coerce_action(value: str, *, needs_human: bool) -> str:
    if needs_human:
        return "no_action"
    return value if value in VALID_ACTIONS else "no_action"


def _build_todo(
    result: ClusterMerge,
    signals: Sequence[Signal],
    *,
    now: datetime,
    urgency_settings: UrgencySettings,
    senders: ImportantSenders,
    seen_in_runs: int = 1,
) -> TodoItem:
    sources = _merged_sources(signals)
    due = earliest_explicit_due(list(signals))
    urgency = compute_urgency(
        due=due, sources=sources, now=now, settings=urgency_settings, senders=senders
    )
    return TodoItem(
        id=todo_id(result.statement, [(s.kind, s.id) for s in sources]),
        title=result.title[:120],
        detail=result.detail or result.statement,
        owner=_coerce_owner(result.owner),  # type: ignore[arg-type]
        due=due,
        urgency=urgency,
        urgency_reason=result.urgency_reason,
        suggested_action=_coerce_action(  # type: ignore[arg-type]
            result.suggested_action, needs_human=result.needs_human_judgment
        ),
        confidence=result.confidence,
        needs_human_judgment=result.needs_human_judgment,
        sources=sources,
        seen_in_runs=seen_in_runs,
    )


def _partition(groups: list[list[int]] | None, signals: Sequence[Signal]) -> list[list[Signal]]:
    """Turn the model's split into groups of signals, repairing an incomplete partition.

    A dropped index would silently lose an ask, which is the one outcome this system
    exists to prevent — so anything the model failed to place becomes its own group
    rather than disappearing.
    """
    if not groups:
        return [[s] for s in signals]

    placed: set[int] = set()
    result: list[list[Signal]] = []
    for group in groups:
        members = [i for i in group if 0 <= i < len(signals) and i not in placed]
        placed.update(members)
        if members:
            result.append([signals[i] for i in members])

    missing = [i for i in range(len(signals)) if i not in placed]
    if missing:
        log.warning("split dropped indices; recovering them as singletons", missing=missing)
        result.extend([[signals[i]] for i in missing])
    return result


def _cluster_label(signals: Sequence[Signal]) -> str:
    """A short human name for a cluster, for the console timeline."""
    head = signals[0].statement if signals else "cluster"
    head = head.strip().rstrip(".")
    if len(head) > 58:
        head = head[:57].rstrip() + "\u2026"
    return f"{head} ({len(signals)})"


async def merge_cluster(
    runner: AgentRunner,
    tier: ModelTier,
    signals: Sequence[Signal],
    *,
    now: datetime,
    urgency_settings: UrgencySettings,
    senders: ImportantSenders,
    instructions: str,
    operator: str,
    parent: str = "chief-of-staff",
) -> list[TodoItem]:
    """Resolve one candidate cluster into one or more to-dos.

    `parent` is recorded, not acted on. A top-level cluster is delegated by the
    orchestrator; the groups of a split are re-asked by the consolidator itself, so those
    calls carry the consolidator as their parent. That is what makes a split legible as
    recursion in the console instead of looking like five unrelated calls.
    """
    sources = _merged_sources(signals)
    due = earliest_explicit_due(list(signals))
    urgency_view = explain_inputs(
        due=due, sources=sources, now=now, settings=urgency_settings, senders=senders
    )

    result = await runner.call(
        agent=AGENT,
        tier=tier,
        instructions=instructions,
        prompt=render_cluster(signals, urgency_view, operator=operator),
        schema=ClusterMerge,
        stage="consolidate",
        parent=parent,
        label=_cluster_label(signals),
    )

    if result.merge or len(signals) == 1:
        return [
            _build_todo(
                result,
                signals,
                now=now,
                urgency_settings=urgency_settings,
                senders=senders,
            )
        ]

    # A split means the cluster-level answer describes a question that turned out to be
    # the wrong question. Its title, its suggested action, and above all its
    # urgency_reason belong to a group of asks that does not exist. Reusing them produces
    # a to-do titled after the split itself, and a reason quoting a score the item does
    # not have — which is precisely the contradiction the constitution forbids.
    #
    # So each group is re-asked as its own cluster. Splits are rare, the extra calls are
    # cheap, and the alternative is a wrong reason on every split item.
    groups = _partition(result.split_groups, signals)
    if len(groups) == 1:
        # The model said split but produced one group. Take it as a merge rather than
        # recursing forever on the same input.
        return [
            _build_todo(
                result, signals, now=now, urgency_settings=urgency_settings, senders=senders
            )
        ]

    todos: list[TodoItem] = []
    for group in groups:
        todos.extend(
            await merge_cluster(
                runner,
                tier,
                group,
                now=now,
                urgency_settings=urgency_settings,
                senders=senders,
                instructions=instructions,
                operator=operator,
                parent=AGENT,
            )
        )
    log.info("cluster split", into=len(todos), calls=len(groups) + 1)
    return todos


async def consolidate(
    runner: AgentRunner,
    tier: ModelTier,
    signals: Sequence[Signal],
    clusters: Sequence[Cluster],
    *,
    now: datetime,
    urgency_settings: UrgencySettings,
    senders: ImportantSenders,
    instructions: str,
    operator: str,
) -> list[TodoItem]:
    """Every candidate cluster through Stage B, ranked highest urgency first.

    Clusters are independent, so they run concurrently. Sequentially, a dozen clusters is
    over a minute of wall time and the operator is watching a blank terminal; the budget
    in SC-014 is two minutes for the whole run, not for this stage.

    Determinism is unaffected: `gather` preserves order, and the final sort is on urgency
    and title rather than completion time.
    """
    if not clusters:
        return []

    results = await asyncio.gather(
        *(
            merge_cluster(
                runner,
                tier,
                [signals[i] for i in cluster.indices],
                now=now,
                urgency_settings=urgency_settings,
                senders=senders,
                instructions=instructions,
                operator=operator,
            )
            for cluster in clusters
        )
    )

    todos = [todo for group in results for todo in group]
    todos.sort(key=lambda t: (-t.urgency, t.title))
    log.info("consolidated", clusters=len(clusters), todos=len(todos))
    return todos
