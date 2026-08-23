#!/usr/bin/env python
"""The single-agent baseline — and why it is not obviously wrong.

This is `ckpt-1-naive`, and the first live thing the audience sees. One prompt, all the
context, "give me my to-dos". No specialists, no deterministic pre-pass, no consolidation
stage, no provenance.

**It is not a straw man, and in 2026 it does not fail the way you would expect.** Run it
against the corpus with a current strong model and the output looks good. It usually
finds the buried commitment. It usually refuses to invent a date from "soon-ish". It
often merges the triple correctly.

That is the actual hook, and it is more interesting than "the naive one is dumb":

  1. **Nothing links back to anything.** No message ids, no permalinks, no timestamps.
     You cannot check a single item without going and reading the mailbox yourself — at
     which point what did the assistant save you?

  2. **It merges things it should not.** Priya asking for the renewal number and Dana
     asking to cancel the renewal entirely land in one item, and one of the two asks
     simply disappears. A wrong merge hides work; you never learn it existed.

  3. **Run it twice and you get different answers.** Six to-dos, then eight. Different
     groupings, different wording, different count. There are no stable identifiers, so
     there is no clean diff, so there is nothing to review, so nothing downstream can be
     gated on it. It is a chat answer, not a pipeline.

Use `--twice` on stage. Running it twice in front of people and getting two different
lists is worth more than any slide about non-determinism.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from azure.identity.aio import AzureCliCredential
from pydantic import BaseModel, ConfigDict, Field

from cos.agents.runner import AgentRunner, ModelTier
from cos.corpus import load
from cos.logging import configure, get_logger
from cos.manifest import RunRecorder, new_run_id
from cos.settings import load_environment, load_settings

log = get_logger("naive")

NAIVE_INSTRUCTIONS = """
You are a helpful assistant. Read the user's mail, chat, and calendar, and give them
their to-do list for today. Include deadlines where you can work them out. Be concise and
useful.
"""


class NaiveTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    due: str | None = Field(default=None, description="A deadline, if there is one.")
    why: str = ""


class NaiveList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    todos: list[NaiveTodo] = Field(default_factory=list)


def render_everything(bundle: object) -> str:
    """All of it, in one prompt. That is the point."""
    from cos.sources.collect import SourceBundle

    assert isinstance(bundle, SourceBundle)
    lines = ["=== EMAIL ==="]
    for m in bundle.mail:
        who = "me" if m.is_from_operator else (m.from_name or m.from_address)
        lines.append(f"From {who} — {m.subject}\n{m.body_text[:900]}\n")
    lines.append("=== CHAT ===")
    for c in bundle.chat:
        who = "me" if c.is_from_operator else (c.from_name or "unknown")
        lines.append(f"{who}: {c.body_text}")
    lines.append("\n=== CALENDAR ===")
    for e in bundle.events:
        lines.append(f"{e.start:%a %d %b %H:%M} {e.subject}\n{e.body_text[:400]}")
    return "\n".join(lines)


async def main() -> int:
    configure()
    settings, env = load_settings(), load_environment()
    env.require("foundry_project_endpoint", "azure_tenant_id")
    endpoint, tenant = env.foundry_project_endpoint, env.azure_tenant_id
    assert endpoint and tenant

    bundle, now, _ = load()
    recorder = RunRecorder(
        run_id=new_run_id(datetime.now(UTC)),
        window_start=now - timedelta(days=7),
        window_end=now,
    )

    tier = ModelTier(
        deployment=settings.models.strong.deployment,
        version=settings.models.strong.version,
    )

    async with (
        AzureCliCredential(tenant_id=tenant) as credential,
        AgentRunner(project_endpoint=endpoint, credential=credential, recorder=recorder) as runner,
    ):
        result = await runner.call(
            agent="naive-baseline",
            tier=tier,
            instructions=NAIVE_INSTRUCTIONS,
            prompt=render_everything(bundle),
            schema=NaiveList,
        )

    print(f"\n{bundle.total} items in, one prompt, {len(result.todos)} to-dos out\n")
    for todo in result.todos:
        due = todo.due or "—"
        print(f"  [{due:>12}]  {todo.title}")
        if todo.why:
            print(f"                  {todo.why[:88]}")

    manifest = recorder.finish()
    print(
        f"\n{manifest.model_calls} call, "
        f"{manifest.input_tokens:,}/{manifest.output_tokens:,} tokens, "
        f"${manifest.estimated_cost_usd:.3f}, {manifest.wall_seconds:.1f}s"
    )
    print("\nNo provenance. Nothing here links back to a message you could check.")
    return len(result.todos)


async def twice() -> int:
    """Run it twice and show that the answer moved."""
    first = await main()
    print("\n" + "=" * 72)
    print("SAME INPUT. SAME MODEL. AGAIN.")
    print("=" * 72)
    second = await main()
    print("\n" + "=" * 72)
    print(f"  first run:  {first} to-dos")
    print(f"  second run: {second} to-dos")
    print("\n  No stable identifiers. No clean diff. Nothing to review, and so nothing")
    print("  downstream can be gated on it. That is the problem — not the wording.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    import sys

    if "--twice" in sys.argv:
        raise SystemExit(asyncio.run(twice()))
    asyncio.run(main())
    raise SystemExit(0)
