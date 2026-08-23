# Implementation Plan: Chief of Staff Pipeline

**Branch**: `001-chief-of-staff-pipeline` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-chief-of-staff-pipeline/spec.md`

## Summary

Retrieve a window of live Microsoft 365 mail, chat, and calendar; extract signals from each
source with a specialist agent; deduplicate them into a ranked to-do list; draft the actions
that would clear it; and stop. The proposals land as markdown files in a pull request, and
only a merge plus an approval on a protected environment performs them.

The technical shape follows from one distinction. Which sources matter varies per request,
so the three ingest agents are Foundry connected agents beneath an orchestrator and the
model routes. The order of consolidate-then-draft never varies, so those two are called from
code. Within consolidation, clustering is deterministic Python and only candidate clusters
reach a model. State lives in the repository — pending queue, sent queue, ledger — because
the audit trail is the product.

## Technical Context

**Language/Version**: Python 3.11+ (`requires-python = ">=3.11"`), managed with `uv`, lockfile committed.

**Primary Dependencies**: `agent-framework` 1.15.0 (orchestration) with `agent-framework-azure-ai` 1.0.0rc6 (Foundry provider, pinned exactly — it is a release candidate); `azure-ai-projects` 2.5.0 (agent provisioning and connected agents); `azure-identity` 1.25.x and `msal` 1.37.x (two separate credential chains, see research R-003); `httpx` (Graph v1.0, see R-004); `pydantic` 2.13.x (contracts); `typer` (CLI); `structlog` (logging); `python-ulid`; `PyYAML`; `python-frontmatter`.

**Storage**: Files in the repository. `outbox/{pending,sent,failed}/*.md`, `state/ledger.json`, `state/runs/*.jsonl`, `BRIEF.md`. No database, no Azure state store — Constitution VII.

**Testing**: `pytest`, with a custom `httpx` transport for fixture replay. Four suites: contracts, deterministic pre-pass, executor idempotency, and a scored consolidator evaluation against `tests/golden/`.

**Target Platform**: macOS and Linux for the operator CLI; `ubuntu-latest` GitHub Actions runners for the scheduled and gated workflows.

**Project Type**: Single Python package plus a CLI, with two GitHub Actions workflows acting as the pipeline.

**Performance Goals**: A full run over 25–40 messages completes in under two minutes wall time (SC-014). The deterministic pre-pass is negligible against the model calls; the budget is spent on one orchestrated ingest pass, one merge call per candidate cluster, and one draft call per actionable to-do.

**Constraints**: Model versions pinned with `NoAutoUpgrade`, so behaviour cannot shift between rehearsal and performance. Maximum five actions per run, hard stop. Dry-run by default. Recipient allowlist enforced at execution time against file contents. CI runs with no network path to Microsoft Graph and no credentials present.

**Scale/Scope**: One operator, one mailbox, one window per run. Roughly 40 messages, a dozen to-dos, under five actions. Six agent definitions, of which three are connected and two are code-called.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Gate | Pre-design | Post-design |
|-----------|------|------------|-------------|
| I. Two gates before any send | No code path performs an outbound action outside `execute.yml`, which is bound to the `send` environment. Allowlist, dry-run default, and per-run maximum are enforced in `outbox/executor.py`, not in the workflow. | PASS | PASS — `executor.py` takes an injected transport and refuses to construct a live one under `dry_run`; `cli.py execute` cannot bypass it. |
| II. Idempotency | Ledger consulted before every action; identifiers are content-derived. | PASS | PASS — `ledger.check_and_reserve()` is the only path to a send, and `test_idempotency.py` drives the executor twice. |
| III. Determinism over inference | Clustering and ranking contain no model call. | PASS | PASS — `consolidate/prepass.py` and `consolidate/rank.py` import nothing from `cos.agents`, enforced by an import-graph test. |
| IV. Provenance | Every model carries a non-empty `sources: list[SourceRef]`. | PASS | PASS — enforced by Pydantic `min_length=1`, so it is a contract violation rather than a review comment. |
| V. Contracts validated, never coerced | Single retry, then loud failure. No coercion. | PASS | PASS — the retry lives in one place, `agents/runner.py`, so there is no second implementation to drift. |
| VI. Deterministic tests | CI green with no tenant and no network. | PASS | PASS — the fixture transport fails loudly on a cache miss, so an accidental live call in CI is a test failure rather than a silent pass. |
| VII. Repository is source of truth | Agents provisioned from `agents/*.yaml`; infra from `infra/`. | PASS | PASS — `provision_agents.py` is idempotent and re-runnable before each rehearsal. |
| VIII. Observable by default | Every model call logged; manifest reaches the pull request. | PASS | PASS — logging wraps the runner, so an unlogged call would require bypassing the only call path. |

No violations. Complexity Tracking is therefore omitted.

## Project Structure

### Documentation (this feature)

```text
specs/001-chief-of-staff-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output — JSON Schemas and the file/workflow contracts
└── tasks.md             # Phase 2 output, from /speckit-tasks
```

### Source Code (repository root)

```text
agents/                          # Agent definitions. The repository is the source of truth.
├── chief-of-staff.yaml          #   orchestrator, owns the three connected agents
├── mail-triage.yaml             #   connected, cheap tier
├── chat-triage.yaml             #   connected, cheap tier
├── calendar-context.yaml        #   connected, cheap tier
├── consolidator.yaml            #   code-called, strong tier
└── drafter.yaml                 #   code-called, strong tier

config/
├── settings.yaml                # window, dry_run, max_actions, pinned model tiers, weights
├── allowed_recipients.yaml      # the kill switch
├── important_senders.yaml       # feeds urgency; explicit, never inferred
└── voice.md                     # writing rules and sample sent mail

infra/                           # Bicep composed from Azure Verified Modules
├── main.bicep
└── main.bicepparam

src/cos/
├── models.py                    # the contracts
├── settings.py                  # typed config loading
├── graph/
│   ├── auth.py                  # MSAL device code, cached; app credentials for CI
│   └── client.py                # httpx against Graph v1.0, backoff, delta, replayable
├── sources/
│   ├── mail.py                  # retrieval + normalisation to MailMessage
│   ├── chat.py                  # retrieval + normalisation to ChatMessage
│   └── calendar.py              # retrieval + normalisation to CalendarEvent
├── agents/
│   ├── provision.py             # YAML -> Foundry, idempotent
│   ├── connected.py             # orchestrator wiring for the three ingest agents
│   └── runner.py                # the single call path: schema, validate, retry once, log
├── consolidate/
│   ├── prepass.py               # deterministic clustering. No model. Ever.
│   ├── entities.py              # regex entity-key extraction
│   ├── merge.py                 # LLM merge-or-split within a candidate cluster
│   └── rank.py                  # urgency arithmetic, pure
├── draft/
│   ├── drafter.py               # one call per actionable to-do
│   └── voice.py                 # loads and renders config/voice.md
├── outbox/
│   ├── writer.py                # ProposedAction -> frontmatter markdown
│   ├── reader.py                # markdown -> ProposedAction, for the executor
│   ├── ledger.py                # check-and-reserve, receipts
│   ├── executor.py              # Graph and GitHub writes, allowlist, dry-run
│   └── pr.py                    # branch, commit, open pull request
├── brief.py                     # BRIEF.md rendering
├── manifest.py                  # run manifest and token accounting
└── cli.py                       # typer: login, brief, propose, execute, replay

scripts/
├── provision_agents.py
├── auth_login.py
├── run_pipeline.py
├── record_fixtures.py
└── seed_demo_inbox.py           # talk scaffolding, deliberately separate from src/

tests/
├── fixtures/                    # recorded Graph payloads, redacted at capture
├── golden/                      # expected TodoItem[] for the seeded corpus
├── test_contracts.py
├── test_prepass.py              # the majority of the suite. No model in the loop.
├── test_rank.py
├── test_ids.py                  # stability of content-derived identifiers
├── test_idempotency.py
├── test_allowlist.py
├── test_determinism_imports.py  # Principle III, enforced by import graph
└── test_eval_consolidator.py    # scored, tolerant, thresholded

outbox/{pending,sent,failed}/
state/ledger.json
state/runs/

.github/workflows/
├── brief.yml                    # scheduled and manual: run, then open a pull request
├── execute.yml                  # on push to main, gated by the `send` environment
└── ci.yml                       # tests on every pull request
```

**Structure Decision**: a single installable package `cos` under `src/`, with a Typer CLI as
the only entry point and `scripts/` as thin wrappers over it. The internal layout follows the
pipeline stages, which keeps the dependency direction one-way — `sources` knows nothing of
`agents`, `consolidate` knows nothing of `outbox` — and makes Principle III mechanically
checkable: `consolidate.prepass` and `consolidate.rank` must not import `cos.agents`, and a
test asserts it.

`scripts/seed_demo_inbox.py` sits outside `src/` on purpose. It is real code that sends real
mail, but it is scaffolding for a demonstration rather than part of the product, and the
separation should be visible.

## Phase 1 Design Artifacts

- `data-model.md` — the entities, their fields, their invariants, and how identifiers are
  derived.
- `contracts/` — JSON Schemas for the wire contracts, the on-disk proposal file format, the
  ledger record, and the two workflow contracts.
- `quickstart.md` — clean clone to first brief, and the operator's pre-flight sequence.

## Notes carried into implementation

Two constraints from `docs/decisions.md` shape work that has not started yet.

**B-001** is open: the Azure subscription's directory holds no Microsoft 365 licences, so
there is no mailbox to read. It gates only the live-tenant work. Every layer is built and
tested against fixtures, tenant and mailbox arrive from configuration, and pointing the
system at a tenant is a settings change. Task ordering must keep live-tenant work off the
critical path of everything else.

**R-012** stands: the scheduled run covers mail and calendar only, because chat retrieval
under an unattended identity needs an approval that will not arrive in time. The brief says
so when it happens.
