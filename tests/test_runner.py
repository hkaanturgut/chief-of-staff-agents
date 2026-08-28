"""The retry policy — Constitution Principle V.

Retry exactly once with the validation error appended, then fail loudly. No coercion, no
fallback parser, no third attempt.

This is the branch a happy-path live call never reaches and the one most likely to be
wrong, so it is tested with a stub client rather than against a real model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from cos.agents.runner import MAX_ATTEMPTS, AgentCallError, AgentRunner, ModelTier
from cos.manifest import RunRecorder

TIER = ModelTier(deployment="gpt-5.5", version="2026-04-24")


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merge: bool
    statement: str = Field(min_length=1)


@dataclass
class FakeResponse:
    text: str
    usage_details: dict[str, int]
    value: Any = None


class ScriptedClient:
    """Returns each scripted body in turn, and records the prompts it was given."""

    def __init__(self, *bodies: str) -> None:
        self.bodies = list(bodies)
        self.prompts: list[str] = []

    async def get_response(self, message: str, *, options: Any) -> FakeResponse:
        self.prompts.append(message)
        body = self.bodies[min(len(self.prompts) - 1, len(self.bodies) - 1)]
        return FakeResponse(
            text=body,
            usage_details={"input_token_count": 100, "output_token_count": 20},
        )


GOOD = json.dumps({"merge": True, "statement": "one ask"})
MALFORMED = json.dumps({"merge": "yes please", "statement": ""})
EXTRA_FIELD = json.dumps({"merge": True, "statement": "s", "urgency": 90})


@pytest.fixture
def recorder(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> RunRecorder:
    monkeypatch.setattr("cos.manifest.run_log_path", lambda run_id: tmp_path / f"{run_id}.jsonl")
    now = datetime.now(UTC)
    return RunRecorder(run_id="test", window_start=now - timedelta(days=1), window_end=now)


def runner(recorder: RunRecorder, client: ScriptedClient) -> AgentRunner:
    return AgentRunner(
        project_endpoint="https://example.invalid/api/projects/test",
        recorder=recorder,
        client_factory=lambda tier: client,
    )


async def call(r: AgentRunner) -> Answer:
    return await r.call(
        agent="consolidator", tier=TIER, instructions="i", prompt="p", schema=Answer
    )


@pytest.mark.asyncio
async def test_valid_output_on_the_first_attempt_makes_one_call(recorder: RunRecorder) -> None:
    client = ScriptedClient(GOOD)
    result = await call(runner(recorder, client))
    assert result.merge is True
    assert len(client.prompts) == 1
    assert len(recorder.calls) == 1
    assert recorder.calls[0].validation_error is None


@pytest.mark.asyncio
async def test_one_retry_on_validation_failure_then_success(recorder: RunRecorder) -> None:
    client = ScriptedClient(MALFORMED, GOOD)
    result = await call(runner(recorder, client))
    assert result.statement == "one ask"
    assert len(client.prompts) == 2, "expected exactly one retry"


@pytest.mark.asyncio
async def test_the_retry_prompt_carries_the_validation_error(recorder: RunRecorder) -> None:
    """The model is told precisely what it got wrong — not given a looser schema."""
    client = ScriptedClient(MALFORMED, GOOD)
    await call(runner(recorder, client))
    retry = client.prompts[1]
    assert "did not satisfy the required schema" in retry
    assert "statement" in retry or "merge" in retry
    assert retry.startswith("p"), "the retry must keep the original prompt"


@pytest.mark.asyncio
async def test_second_failure_raises_and_does_not_retry_again(recorder: RunRecorder) -> None:
    client = ScriptedClient(MALFORMED)
    with pytest.raises(AgentCallError, match="failed validation"):
        await call(runner(recorder, client))
    assert len(client.prompts) == MAX_ATTEMPTS == 2, "no third attempt"


@pytest.mark.asyncio
async def test_an_invented_field_is_a_failure_not_a_shrug(recorder: RunRecorder) -> None:
    """extra='forbid' means a model inventing a field fails the run (Principle V)."""
    client = ScriptedClient(EXTRA_FIELD)
    with pytest.raises(AgentCallError):
        await call(runner(recorder, client))


@pytest.mark.asyncio
async def test_every_attempt_is_logged_including_the_failed_one(recorder: RunRecorder) -> None:
    """Principle VIII. A failed attempt costs tokens, so it appears in the bill."""
    client = ScriptedClient(MALFORMED, GOOD)
    await call(runner(recorder, client))
    assert len(recorder.calls) == 2
    assert recorder.calls[0].validation_error is not None
    assert recorder.calls[0].attempt == 1
    assert recorder.calls[1].validation_error is None
    assert recorder.calls[1].attempt == 2
    assert recorder.finish().input_tokens == 200


@pytest.mark.asyncio
async def test_the_logical_agent_name_is_what_gets_logged(recorder: RunRecorder) -> None:
    """A cost breakdown should read in terms of the architecture, not model names."""
    await call(runner(recorder, ScriptedClient(GOOD)))
    entry = recorder.calls[0]
    assert entry.agent == "consolidator"
    assert entry.model == "gpt-5.5"
    assert entry.model_version == "2026-04-24"


@pytest.mark.asyncio
async def test_prompts_are_hashed_not_stored(recorder: RunRecorder) -> None:
    """The repository is public and the prompt contains mailbox content."""
    await call(runner(recorder, ScriptedClient(GOOD)))
    entry = recorder.calls[0]
    assert entry.prompt_hash != "p"
    assert len(entry.prompt_hash) == 32
    assert entry.model_dump_json().count('"prompt"') == 0


@pytest.mark.asyncio
async def test_clients_are_reused_per_tier(recorder: RunRecorder) -> None:
    made: list[ModelTier] = []
    client = ScriptedClient(GOOD)

    def factory(tier: ModelTier) -> ScriptedClient:
        made.append(tier)
        return client

    r = AgentRunner(
        project_endpoint="https://example.invalid", recorder=recorder, client_factory=factory
    )
    await call(r)
    await call(r)
    assert len(made) == 1, "the credential behind a client caches; rebuilding wastes auth"
