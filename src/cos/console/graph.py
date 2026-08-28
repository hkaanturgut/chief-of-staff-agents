"""Turning a run log into a delegation graph.

`state/runs/<id>.jsonl` is a flat list of model calls. Each line carries the agent that
ran, the parent that delegated to it, the stage it belongs to, and when it started
relative to the run — which is enough to reconstruct both the graph and the timeline
without any separate tracing system.

Nothing here reads a prompt or a message body. The run log holds prompt *hashes* only, so
the console can show the shape of a run without ever putting mailbox content on screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cos.logging import RUNS_DIR, run_brief_path
from cos.manifest import PRICES_PER_MTOK

# The orchestrator issues no model call of its own — it routes. So it never appears as a
# row in the log, only ever as a parent, and has to be synthesised as a node.
ROOT = "chief-of-staff"

STAGE_ORDER = ["ingest", "consolidate", "draft", ""]


@dataclass(frozen=True)
class Call:
    agent: str
    parent: str
    stage: str
    label: str
    model: str
    model_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    started_ms: int
    attempt: int
    validation_error: str | None

    @property
    def cost_usd(self) -> float:
        price_in, price_out = PRICES_PER_MTOK.get(self.model, (0.0, 0.0))
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


def _call_from_line(raw: dict[str, Any]) -> Call:
    return Call(
        agent=str(raw.get("agent") or "unknown"),
        # Runs recorded before the console existed carry no parent. Attributing them to
        # the orchestrator is the truthful reading: it is what ran them.
        parent=str(raw.get("parent") or ROOT),
        stage=str(raw.get("stage") or ""),
        label=str(raw.get("label") or ""),
        model=str(raw.get("model") or ""),
        model_version=str(raw.get("model_version") or ""),
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        latency_ms=int(raw.get("latency_ms") or 0),
        started_ms=int(raw.get("started_ms") or 0),
        attempt=int(raw.get("attempt") or 1),
        validation_error=raw.get("validation_error"),
    )


def list_runs() -> list[str]:
    """Run ids, newest first, for runs that actually recorded a model call.

    Ordered by modification time rather than by name. Most run ids are timestamps and
    would sort correctly either way, but the recorded fixtures are not — `triple.jsonl`
    sorts after every dated run and would otherwise always be picked as "latest", which
    is exactly the wrong thing to open on stage.
    """
    if not RUNS_DIR.exists():
        return []
    files = [p for p in RUNS_DIR.glob("*.jsonl") if p.stat().st_size > 0]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.stem for p in files]


def latest_run() -> str | None:
    runs = list_runs()
    return runs[0] if runs else None


def read_calls(run_id: str) -> list[Call]:
    path = RUNS_DIR / f"{run_id}.jsonl"
    if not path.exists():
        return []
    calls: list[Call] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            calls.append(_call_from_line(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            # One malformed line must not blank the whole console.
            continue
    calls.sort(key=lambda c: (c.started_ms, c.agent))
    return calls


def _brief_payload(run_id: str) -> dict[str, Any]:
    path: Path = run_brief_path(run_id)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def build(run_id: str) -> dict[str, Any]:
    """The whole run, shaped for the console front end."""
    calls = read_calls(run_id)

    # Nodes are agents. The orchestrator is added even though it never calls a model,
    # because the delegation edges are meaningless without it.
    agents: dict[str, dict[str, Any]] = {
        ROOT: {"id": ROOT, "stage": "orchestrate", "calls": 0, "tokens": 0, "cost": 0.0, "ms": 0}
    }
    edges: dict[tuple[str, str], int] = {}

    for c in calls:
        node = agents.setdefault(
            c.agent,
            {"id": c.agent, "stage": c.stage, "calls": 0, "tokens": 0, "cost": 0.0, "ms": 0},
        )
        node["calls"] += 1
        node["tokens"] += c.input_tokens + c.output_tokens
        node["cost"] = round(float(node["cost"]) + c.cost_usd, 6)
        node["ms"] += c.latency_ms
        if not node["stage"]:
            node["stage"] = c.stage

        agents.setdefault(
            c.parent,
            {"id": c.parent, "stage": "", "calls": 0, "tokens": 0, "cost": 0.0, "ms": 0},
        )
        edges[(c.parent, c.agent)] = edges.get((c.parent, c.agent), 0) + 1

    ordered = sorted(
        agents.values(),
        key=lambda n: (
            STAGE_ORDER.index(n["stage"]) if n["stage"] in STAGE_ORDER else -1,
            str(n["id"]),
        ),
    )

    # Runs recorded before the console existed carry no start offsets. With every call
    # pinned at zero the timeline would draw them all as simultaneous and report a wall
    # clock equal to the single slowest call — confidently wrong. Say so instead.
    has_timing = any(c.started_ms > 0 for c in calls)
    span = max((c.started_ms + c.latency_ms for c in calls), default=0)
    sequential = sum(c.latency_ms for c in calls)
    retries = [c for c in calls if c.validation_error]

    return {
        "run_id": run_id,
        "nodes": ordered,
        "edges": [
            {"source": s, "target": t, "count": n} for (s, t), n in sorted(edges.items())
        ],
        "calls": [
            {
                "agent": c.agent,
                "parent": c.parent,
                "stage": c.stage,
                "label": c.label,
                "model": c.model,
                "model_version": c.model_version,
                "tokens": c.input_tokens + c.output_tokens,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "latency_ms": c.latency_ms,
                "started_ms": c.started_ms,
                "attempt": c.attempt,
                "failed_validation": bool(c.validation_error),
                "cost": round(c.cost_usd, 6),
            }
            for c in calls
        ],
        "span_ms": span if has_timing else sequential,
        "has_timing": has_timing,
        "totals": {
            "calls": len(calls),
            "retries": len(retries),
            "tokens": sum(c.input_tokens + c.output_tokens for c in calls),
            "cost": round(sum(c.cost_usd for c in calls), 4),
            # Wall clock, not the sum of latencies: the ingest agents and the cluster
            # calls run concurrently, so summing them overstates the run by roughly 3x
            # and would quietly misreport the one number the audience cares about.
            "wall_ms": span if has_timing else None,
            "sequential_ms": sequential,
        },
        "brief": _brief_payload(run_id),
    }
