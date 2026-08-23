# Phase 0: Research

Decisions taken before design, with the reasoning that produced them. Everything that the
Technical Context would otherwise have marked NEEDS CLARIFICATION is resolved here.

## R-001 — Orchestration library: Microsoft Agent Framework, with a pinned provider

**Finding.** `agent-framework` is at 1.15.0 and generally available. The Azure AI provider
package, `agent-framework-azure-ai`, is at 1.0.0rc6 — still a release candidate. The
framework meta-package pulls `agent-framework-core[all]==1.15.0`; the Azure provider is a
separate distribution on its own release track.

**Decision.** Use `agent-framework` for orchestration and `agent-framework-azure-ai` for
the Foundry provider, both pinned to exact versions in `pyproject.toml` with a `uv.lock`
committed. A release candidate on the critical path of a live demonstration is a risk that
must be pinned rather than tracked.

**Alternative rejected.** Calling the Foundry REST surface directly through
`azure-ai-projects` alone and hand-rolling the orchestration loop. It removes the
release-candidate dependency but also removes the connected-agent routing that the
architecture exists to demonstrate, and it means writing a tool-call loop that is not the
subject of the talk.

## R-002 — Agent provisioning: `azure-ai-projects`

**Finding.** `azure-ai-projects` 2.5.0 is generally available and is the supported control
surface for creating and updating agents in a Foundry project. It depends on `openai>=3.0.0`
and `azure-core>=1.37.0`, both of which must be allowed to resolve freely rather than
pinned against.

**Decision.** `scripts/provision_agents.py` reads `agents/*.yaml` and creates or updates
each agent through `azure-ai-projects`. Provisioning is idempotent: an agent that already
exists is updated in place by name, so the script can be re-run before every rehearsal
without accumulating duplicates.

The three ingest agents are registered as connected agents beneath the `chief-of-staff`
orchestrator. The consolidator and drafter are declared in `agents/` for consistency of
definition and version control, but are invoked directly from code rather than routed to.

## R-003 — Two credential chains, by necessity

**Finding.** The Foundry account lives in the Visual Studio subscription's directory. That
directory holds no Microsoft 365 licences, so the mailbox must live elsewhere. See
`docs/decisions.md` B-001.

**Decision.** Two explicit credentials, never one ambient default.

- Azure control and data plane: `DefaultAzureCredential` with an explicit
  `tenant_id=AZURE_TENANT_ID`. Locally this resolves the Azure CLI login; in CI it resolves
  the workload identity federated through GitHub OIDC.
- Microsoft Graph: MSAL `PublicClientApplication` with a device-code flow and a persistent
  on-disk token cache, authorising against `GRAPH_TENANT_ID`. In unattended runs, a
  confidential client under the same tenant with application permissions, constrained by an
  Exchange Application Access Policy to the single demo mailbox.

`src/cos/graph/auth.py` therefore builds its own credential and never inherits the ambient
Azure session. This is not a workaround for the demo; it is what the separation looks like
in any deployment where the AI platform subscription and the productivity tenant differ.

## R-004 — Graph access: `httpx` against v1.0, not the generated SDK

**Decision.** Call Microsoft Graph v1.0 through `httpx` with a thin client in
`src/cos/graph/client.py`, rather than taking `msgraph-sdk` as a dependency.

**Reasoning.** The system touches perhaps eight endpoints. The generated SDK brings a large
dependency surface and its own model layer, and everything is normalised into the internal
models at the boundary anyway, so the SDK's types are discarded a line after they are
constructed. A thin client also makes fixture replay trivial: one transport swap, no
monkey-patching of a generated client.

Beta endpoints are excluded. Delta queries are used for mail so repeat runs are cheap.
`429` and `503` retry with exponential backoff honouring `Retry-After`; Graph will throttle,
and it will choose the worst moment.

## R-005 — Structured output: schema-constrained, validated twice

**Decision.** Every agent call requests structured output against the JSON Schema derived
from the relevant Pydantic model, and the response is then validated by Pydantic in
process. Schema constraint at the API and validation in code are complementary: the first
makes malformed output rare, the second makes it detectable.

On `ValidationError`, retry exactly once with the error text appended to the prompt. A
second failure raises and ends the run. Constitution Principle V forbids coercion, so there
is no fallback parser and no field-dropping path.

## R-006 — Deterministic clustering: union-find over three independent keys

**Decision.** Stage A builds candidate clusters with a disjoint-set structure over three
key families computed per signal:

1. Conversation and thread identity, taken directly from the provider.
2. Normalised subject: lowercase, reply and forward prefixes stripped repeatedly, all
   punctuation and runs of whitespace collapsed.
3. Entity keys extracted by regex — issue and ticket references, pull request and document
   URLs with tracking parameters stripped, invoice and order numbers.

Signals sharing any key join the same cluster. The union is deliberately generous: Stage A
optimises for recall, and Stage B is what splits an over-eager grouping. A missed candidate
pair can never be recovered downstream; a wrong one can.

Embedding similarity is deferred. It would add a model dependency, an embedding deployment,
and a threshold to tune, in exchange for candidate pairs that the three key families already
find in the seeded corpus. If it is added later it acts only as a candidate generator and
never as an authority, per Constitution Principle III.

## R-007 — Urgency: a bounded weighted sum, computed in code

**Decision.** Urgency is an integer in 0–100 computed from three components: proximity of an
explicit due date, the configured weight of the sender, and the count of distinct sources
carried by the item. Weights live in `config/settings.yaml`; the sender list lives in
`config/important_senders.yaml`. The function is pure, unit-tested, and produces the same
number for the same input on every run.

The model contributes only `urgency_reason`, a sentence explaining the score it was handed.
It cannot alter the number. This is what makes the polite-but-empty email rank low
reliably — it has no deadline, its sender carries no weight, and it has one source.

## R-008 — Identifier derivation

**Decision.** `TodoItem.id` is a ULID whose randomness is replaced by a BLAKE2b digest over
the normalised merged statement and the sorted source identifiers, with a fixed timestamp
component. The result sorts and reads like a ULID but is a pure function of content, so an
unchanged inbox yields an unchanged identifier and an empty diff.

`ProposedAction.id` derives the same way from its to-do identifier, its kind, and its
targets — which is what makes it usable as an idempotency key in the ledger.

**Alternative rejected.** A random ULID plus a separate content-hash field. It works, but
the identifier is what appears in filenames, in the ledger, and in the pull request diff.
Making the visible identifier the stable one is what produces the clean second-run diff
that demonstrates the property.

## R-009 — Ledger: an append-only JSON file

**Decision.** `state/ledger.json` holds one record per performed action: identifier, kind,
performed-at timestamp, the provider's returned message identifier, and the pull request
that authorised it. It is read fully, checked, appended to, and committed back to `main`.

**Reasoning.** The repository is the state, per Constitution Principle VII. A file in git
gives durability, an audit trail, and human readability for free, and the volume — single
digits per run — is nowhere near where a file stops being appropriate. A database would add
a resource, a credential, and a second source of truth.

The commit back to `main` carries `[skip ci]` so that recording a send does not trigger the
workflow that performs sends.

## R-010 — Fixture capture and replay

**Decision.** `scripts/record_fixtures.py` performs real Graph calls and writes redacted
response bodies to `tests/fixtures/`, keyed by a hash of method, path, and sorted query
parameters. Redaction happens at capture time, before anything reaches disk: display names
and addresses are replaced with stable pseudonyms from a mapping, message bodies are kept
because they are already fictional, and tokens and identifiers that could identify a real
tenant are rewritten.

Replay swaps the `httpx` transport for one that resolves the same key from disk and fails
loudly on a miss. Tests therefore need no network, no tenant, and no credential, satisfying
SC-009.

## R-011 — Model call logging

**Decision.** Every call writes one JSON line to `state/runs/<run-id>.jsonl`: agent name,
model name and pinned version, a BLAKE2b hash of the rendered prompt, input and output
token counts, latency in milliseconds, and whether it was a validation retry. The run
manifest aggregates these into totals, and the totals reach the pull request description.

Prompts are hashed rather than stored. The hash proves which prompt ran and detects drift
between rehearsal and performance without committing mailbox content to a public
repository.

## R-012 — Chat under unattended identity is out of reach, and stays out

**Finding.** Reading Teams chat messages with application permissions requires Microsoft's
protected API approval, a review process measured in days to weeks.

**Decision.** The scheduled run covers mail and calendar. Chat is retrieved only in attended
runs under delegated authority. `brief.yml` sets the source list accordingly, and any brief
produced without chat states which source was absent, so a reader is never misled about
coverage. FR-045 and FR-009 exist to make this visible rather than silent.
