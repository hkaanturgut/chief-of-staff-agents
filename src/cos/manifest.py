"""Run accounting.

Every model call is logged with a prompt hash, the pinned model version, token counts,
and latency (Constitution VIII). The totals become the header of the pull request
description, because the cost and latency of an agent pipeline are things a reviewer
needs in front of them at review time.

Prompts are hashed, never stored. The hash proves which prompt ran and detects drift
between rehearsal and performance, without committing mailbox content to a public
repository.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cos.logging import run_log_path
from cos.models import ModelCallLog, RunManifest

# Approximate USD per million tokens, for the figure in the pull request body. It is a
# guide to the order of magnitude, not an invoice — and it is labelled that way where it
# is displayed.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.25, 2.00),
    "gpt-5.5": (1.25, 10.00),
}


def prompt_hash(prompt: str) -> str:
    return hashlib.blake2b(prompt.encode("utf-8"), digest_size=16).hexdigest()


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")


@dataclass
class RunRecorder:
    """Accumulates one run's model calls and turns them into a manifest."""

    run_id: str
    window_start: datetime
    window_end: datetime
    dry_run: bool = True
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    sources_requested: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)

    counts: dict[str, int] = field(default_factory=dict)
    calls: list[ModelCallLog] = field(default_factory=list)

    def count(self, key: str, value: int) -> None:
        self.counts[key] = value

    def source_result(self, name: str, *, ok: bool) -> None:
        (self.sources_succeeded if ok else self.sources_failed).append(name)

    def record_call(
        self,
        *,
        agent: str,
        model: str,
        model_version: str,
        prompt: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        attempt: int = 1,
        validation_error: str | None = None,
    ) -> ModelCallLog:
        entry = ModelCallLog(
            run_id=self.run_id,
            agent=agent,
            model=model,
            model_version=model_version,
            prompt_hash=prompt_hash(prompt),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            attempt=attempt,
            validation_error=validation_error,
        )
        self.calls.append(entry)
        with run_log_path(self.run_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.model_dump(mode="json"), sort_keys=True) + "\n")
        return entry

    @property
    def estimated_cost_usd(self) -> float:
        total = 0.0
        for c in self.calls:
            price_in, price_out = PRICES_PER_MTOK.get(c.model, (0.0, 0.0))
            total += (c.input_tokens * price_in + c.output_tokens * price_out) / 1_000_000
        return round(total, 4)

    def finish(self, now: datetime | None = None) -> RunManifest:
        finished = now or datetime.now(UTC)
        return RunManifest(
            run_id=self.run_id,
            started_at=self.started_at,
            finished_at=finished,
            window_start=self.window_start,
            window_end=self.window_end,
            sources_requested=self.sources_requested,
            sources_succeeded=self.sources_succeeded,
            sources_failed=self.sources_failed,
            messages_in=self.counts.get("messages_in", 0),
            signals=self.counts.get("signals", 0),
            clusters=self.counts.get("clusters", 0),
            todos=self.counts.get("todos", 0),
            actions=self.counts.get("actions", 0),
            model_calls=len(self.calls),
            input_tokens=sum(c.input_tokens for c in self.calls),
            output_tokens=sum(c.output_tokens for c in self.calls),
            estimated_cost_usd=self.estimated_cost_usd,
            wall_seconds=round((finished - self.started_at).total_seconds(), 2),
            dry_run=self.dry_run,
        )
