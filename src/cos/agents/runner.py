"""The single model call path.

Every model call in the system goes through `AgentRunner.call`. That is deliberate: it
means schema constraint, validation, the retry policy, and logging exist in exactly one
place and cannot drift between callers.

The retry policy is Constitution Principle V, in code: on a validation failure, retry
**once** with the error appended to the prompt, then fail the run loudly. There is no
fallback parser, no coercion, and no `except: pass`. A pipeline that quietly repairs
malformed model output hides the exact signal telling you the prompt is wrong.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, TypeVar

from agent_framework import ChatOptions
from agent_framework_foundry import FoundryChatClient
from azure.core.credentials_async import AsyncTokenCredential
from pydantic import BaseModel, ValidationError

from cos.logging import get_logger
from cos.manifest import RunRecorder

T = TypeVar("T", bound=BaseModel)

log = get_logger("agents.runner")

MAX_ATTEMPTS = 2  # the first call, plus exactly one retry


class AgentCallError(RuntimeError):
    """Raised when a model call fails validation twice. Ends the run."""


class ChatClient(Protocol):
    """The slice of the provider client this module actually uses.

    Narrow on purpose. It is what lets `tests/test_runner.py` exercise the retry policy
    with a stub instead of a live model — the retry path is the one branch that must be
    right and that a happy-path live call never reaches.
    """

    async def get_response(self, message: str, *, options: Any) -> Any: ...


@dataclass(frozen=True)
class ModelTier:
    """A pinned deployment. The version is carried so it reaches the logs and the PR."""

    deployment: str
    version: str


class AgentRunner:
    """Calls Foundry models with structured output, validation, and accounting.

    One client per tier. Constructing a client is cheap, but the token credential behind
    it caches, so reusing them across a run avoids re-authenticating per call.
    """

    def __init__(
        self,
        *,
        project_endpoint: str,
        credential: AsyncTokenCredential | None = None,
        recorder: RunRecorder,
        client_factory: Callable[[ModelTier], ChatClient] | None = None,
    ) -> None:
        self._endpoint = project_endpoint
        self._credential = credential
        self._recorder = recorder
        self._client_factory = client_factory or self._build_client
        self._clients: dict[str, ChatClient] = {}

    def _build_client(self, tier: ModelTier) -> ChatClient:
        client: ChatClient = FoundryChatClient(
            project_endpoint=self._endpoint,
            model=tier.deployment,
            credential=self._credential,
        )
        return client

    def _client(self, tier: ModelTier) -> ChatClient:
        if tier.deployment not in self._clients:
            self._clients[tier.deployment] = self._client_factory(tier)
        return self._clients[tier.deployment]

    async def call(
        self,
        *,
        agent: str,
        tier: ModelTier,
        instructions: str,
        prompt: str,
        schema: type[T],
        stage: str = "",
        parent: str | None = None,
        label: str | None = None,
    ) -> T:
        """Call a model and return validated output, or raise.

        `agent` is the logical agent name — `mail-triage`, `consolidator` — not the
        deployment. It is what appears in the run log, so a cost breakdown reads in terms
        of the architecture rather than in terms of model names.

        `stage`, `parent`, and `label` are what turn the run log into a delegation graph
        rather than a flat list of calls. They are descriptive only: nothing in the
        pipeline branches on them, so a caller that omits them still gets identical
        behaviour and merely a thinner picture in the console.
        """
        client = self._client(tier)
        options = ChatOptions(response_format=schema, instructions=instructions)

        message = prompt
        last_error: ValidationError | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            started_ms = self._recorder.elapsed_ms()
            started = time.perf_counter()
            response = await client.get_response(message, options=options)
            latency_ms = round((time.perf_counter() - started) * 1000)

            usage = response.usage_details or {}
            input_tokens = int(usage.get("input_token_count", 0) or 0)
            output_tokens = int(usage.get("output_token_count", 0) or 0)

            try:
                value = self._validate(response, schema)
            except ValidationError as exc:
                last_error = exc
                self._recorder.record_call(
                    agent=agent,
                    model=tier.deployment,
                    model_version=tier.version,
                    prompt=message,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    validation_error=str(exc)[:2000],
                    stage=stage,
                    parent=parent,
                    label=label,
                    started_ms=started_ms,
                )
                log.warning(
                    "structured output failed validation",
                    agent=agent,
                    attempt=attempt,
                    errors=exc.error_count(),
                )
                if attempt == MAX_ATTEMPTS:
                    break
                # Retry once, with the error appended. Not a different prompt and not a
                # looser schema — the model is told precisely what it got wrong.
                message = (
                    f"{prompt}\n\n"
                    "Your previous response did not satisfy the required schema. "
                    f"Fix exactly these problems and return valid output:\n{exc}"
                )
                continue

            self._recorder.record_call(
                agent=agent,
                model=tier.deployment,
                model_version=tier.version,
                prompt=message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                attempt=attempt,
                stage=stage,
                parent=parent,
                label=label,
                started_ms=started_ms,
            )
            log.info(
                "model call",
                agent=agent,
                model=tier.deployment,
                attempt=attempt,
                latency_ms=latency_ms,
                tokens=input_tokens + output_tokens,
            )
            return value

        raise AgentCallError(
            f"{agent}: structured output failed validation on {MAX_ATTEMPTS} attempts "
            f"against {schema.__name__}. Last error:\n{last_error}"
        )

    @staticmethod
    def _validate(response: object, schema: type[T]) -> T:
        """Validate the response against the schema.

        The client parses into the schema itself, but we re-validate rather than trust
        it. Two reasons: the parse path can change under us across provider versions, and
        an already-correct object costs microseconds to re-check. Trusting a library to
        have enforced our own contract is how the contract stops being enforced.
        """
        value = getattr(response, "value", None)
        if isinstance(value, schema):
            return schema.model_validate(value.model_dump())
        text = getattr(response, "text", "") or ""
        return schema.model_validate_json(text)

    async def __aenter__(self) -> AgentRunner:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._clients.clear()
