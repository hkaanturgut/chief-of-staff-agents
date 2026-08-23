---

description: "Task list for the Chief of Staff pipeline"
---

# Tasks: Chief of Staff Pipeline

**Input**: Design documents from `specs/001-chief-of-staff-pipeline/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: included, and not optional here. The specification makes testability a
first-class requirement (SC-009 through SC-011) and the constitution makes it Principle VI.
The deterministic pre-pass must carry the majority of the suite.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on incomplete work
- **[Story]**: US1 (the brief), US2 (the pull request), US3 (execution)

## Path Conventions

Single project. `src/cos/` for the package, `tests/` at the repository root, `agents/` and
`config/` for declarations, `.github/workflows/` for the pipeline.

---

## Phase 1: Setup

**Purpose**: a runnable, testable, lintable skeleton. Nothing here depends on a tenant.

- [x] T001 Create `pyproject.toml` declaring package `cos` with `requires-python = ">=3.11"`, the src layout, the `cos` console script, and the pinned dependencies from plan.md Technical Context
- [x] T002 Run `uv sync` and commit `uv.lock`, so a clean clone resolves identically (SC-015)
- [x] T003 [P] Configure ruff and mypy in `pyproject.toml`, targeting `src/cos` with strict optional handling
- [x] T004 [P] Create the directory skeleton with `__init__.py` files: `src/cos/{graph,sources,agents,consolidate,draft,outbox}`, `tests/{fixtures,golden}`, `outbox/{pending,sent,failed}` each with a `.gitkeep`, and `state/runs/`
- [x] T005 [P] Add `state/ledger.json` containing `{"entries": []}` and confirm `.gitignore` covers `.env`, `__pycache__`, `.venv`, and the MSAL token cache but **not** `state/` — the ledger and run logs are the audit trail and belong in version control
- [x] T006 [P] Write `config/settings.yaml` with `dry_run: true`, `max_actions_per_run: 5`, the window defaults from D-008, the pinned model deployments and versions, and the urgency weights from R-007
- [x] T007 [P] Write `config/allowed_recipients.yaml` with an explanatory header and no live addresses yet, and `config/important_senders.yaml` with the schema from D-009
- [x] T008 [P] Write `config/voice.md` with placeholder writing rules and slots for three to five sample sent messages

---

## Phase 2: Foundational

**⚠️ Blocking.** No user story can begin until this phase is complete.

- [x] T009 Implement all models from data-model.md in `src/cos/models.py`, with `extra="forbid"` and `frozen=True` throughout, `min_length=1` on every `sources` field, the validator rejecting a non-null `due` when `ambiguous` is true, the validator forcing `suggested_action="no_action"` when `needs_human_judgment` is true, and timezone-aware datetime enforcement
- [x] T010 Implement content-derived identifiers in `src/cos/ids.py` per data-model.md: `normalise()`, `ulid_from_hash()`, `todo_id()`, `action_id()`
- [x] T011 [P] Write `tests/test_contracts.py`: every invariant in data-model.md "Invariants worth restating", each as a failing-construction test
- [x] T012 [P] Write `tests/test_ids.py`: same content yields the same identifier across processes; reordered sources yield the same identifier; a changed statement yields a different one
- [x] T013 Implement typed settings loading in `src/cos/settings.py`, reading `config/*.yaml` and `.env` into frozen Pydantic models, failing loudly on a missing required key
- [x] T014 Implement structured logging setup in `src/cos/logging.py` using `structlog`, with a JSON renderer for `state/runs/` and a human renderer for the terminal
- [x] T015 Implement `src/cos/manifest.py`: `RunManifest` accumulation, token and cost totals, and `ModelCallLog` line writing to `state/runs/<run_id>.jsonl`
- [x] T016 Implement `scripts/export_schemas.py`, generating the committed JSON Schemas in `specs/001-chief-of-staff-pipeline/contracts/` from the Pydantic models
- [x] T017 [P] Write `tests/test_schemas_current.py`, asserting the committed schemas match what the models currently produce, so a model change that is not reflected in the contracts fails CI
- [x] T018 Implement the Typer CLI skeleton in `src/cos/cli.py` with `login`, `provision`, `brief`, `propose`, `execute`, and `replay`, each wired to a stub that exits non-zero
- [x] T019 Write `.github/workflows/ci.yml` per contracts/workflows.md: `uv sync --frozen` then `pytest`, `contents: read` only, no credentials of any kind
- [x] T020 [P] Write `tests/test_determinism_imports.py`, asserting by import-graph inspection that `cos.consolidate.prepass`, `cos.consolidate.entities`, and `cos.consolidate.rank` transitively import nothing from `cos.agents` (Constitution III)

**Checkpoint**: `uv run pytest` is green and `uv run cos --help` works, with no tenant and no network.

---

## Phase 3: User Story 1 — one honest to-do list (Priority P1) 🎯 MVP

**Goal**: `cos brief` produces a ranked, deduplicated, fully-sourced `BRIEF.md` from a
window of mail, chat, and calendar.

**Independent test**: run against the seeded mailbox and confirm the six planted traps from
spec.md are handled — the triple collapses to one item with three sources, the buried
commitment appears, "soon-ish" yields no due date, and the polite-but-empty email ranks low.

### Graph access and normalisation

- [x] T021 [US1] Implement `src/cos/graph/auth.py`: MSAL `PublicClientApplication` device-code flow against `GRAPH_TENANT_ID` with a persistent on-disk token cache, plus the confidential-client path for unattended runs. Build the credential explicitly; never inherit the ambient Azure session (R-003)
- [x] T022 [US1] Implement `src/cos/graph/client.py`: `httpx` client for Graph v1.0 with exponential backoff honouring `Retry-After` on 429 and 503, delta-query support for mail, and an injectable transport so fixtures replace the network without monkey-patching (R-004)
- [x] T023 [US1] Wire `cos login` in `src/cos/cli.py` to the device-code flow, and add `scripts/auth_login.py` as a thin wrapper
- [x] T024 [P] [US1] Implement `src/cos/sources/mail.py`: window resolution per D-008, retrieval, HTML-to-text stripping done once, and normalisation to `MailMessage` including `is_from_operator`
- [ ] T025 [P] [US1] Implement `src/cos/sources/chat.py`: retrieval and normalisation to `ChatMessage`, populating `preceding_context` with the prior N messages so implicit asks can be resolved rather than guessed
- [x] T026 [P] [US1] Implement `src/cos/sources/calendar.py`: today plus two business days, normalisation to `CalendarEvent`, retaining invite bodies because they carry asks
- [x] T027 [US1] Implement `src/cos/sources/refs.py`: `SourceRef` construction from each boundary model, including deep-link assembly and excerpt truncation on a word boundary at 240 characters
- [x] T028 [US1] Wire `cos brief --raw` to print normalised source objects, proving auth and retrieval end to end
- [ ] T029 [US1] Implement `scripts/record_fixtures.py`: capture live Graph responses, redact at capture time per R-010 using a stable pseudonym mapping, and key each by a hash of method, path, and sorted query parameters
- [x] T030 [US1] Implement the replay transport in `tests/conftest.py`, resolving fixtures from disk and **failing loudly on a cache miss**, so an accidental live call in CI is a test failure rather than a silent pass
- [x] T031 [P] [US1] Write `tests/test_sources.py`: normalisation from recorded fixtures, window boundary behaviour including the weekend extension, and the timezone-aware requirement

### Agent definitions and the single call path

- [ ] T032 [US1] Write `agents/mail-triage.yaml`: cheap tier, extraction only, the threaded-repetition rule from FR-008, and instructions forbidding summarising, prioritising, and drafting
- [ ] T033 [P] [US1] Write `agents/chat-triage.yaml`: cheap tier, referent resolution from `preceding_context` or `ambiguous: true`, and an explicit prohibition on inventing a deadline
- [ ] T034 [P] [US1] Write `agents/calendar-context.yaml`: cheap tier, `meeting_prep` and `conflict` signals, plus the shape of the day for realistic ranking
- [ ] T035 [P] [US1] Write `agents/chief-of-staff.yaml`: the orchestrator, declaring the three ingest agents as connected agents and describing when each source is relevant
- [x] T036 [US1] Implement `src/cos/agents/runner.py` — the single model call path: schema-constrained request, in-process Pydantic validation, exactly one retry with the validation error appended, loud failure on the second, and a `ModelCallLog` line written for every attempt (Principles V and VIII)
- [ ] T037 [US1] Implement `src/cos/agents/provision.py` and `scripts/provision_agents.py`: read `agents/*.yaml`, create or update by name so re-running is idempotent, and wire the connected-agent relationships through `azure-ai-projects`
- [ ] T038 [US1] Implement `src/cos/agents/connected.py`: orchestrator invocation through `agent-framework`, source selection per request, and manifest population including which sources succeeded and which failed
- [ ] T039 [US1] Wire `cos brief --signals` to print extracted `Signal` objects with their provenance
- [x] T040 [P] [US1] Write `tests/test_runner.py`: the retry fires exactly once on a validation error, the second failure raises, and both attempts are logged

### Consolidation — the centrepiece

- [x] T041 [US1] Implement `src/cos/consolidate/entities.py`: regex extraction of issue and ticket references, pull request and document URLs with tracking parameters stripped, and invoice and order numbers
- [x] T042 [US1] Implement `src/cos/consolidate/prepass.py`: union-find over conversation and thread identity, normalised subject with reply and forward prefixes stripped repeatedly, and shared entity keys. **No model call, ever** (R-006, Constitution III)
- [x] T043 [US1] Write `tests/test_prepass.py` — this should be the largest single test file in the repository. Cover subject normalisation across `Re:`, `RE:`, `Fwd:`, `AW:`, and stacked prefixes; conversation-id grouping; entity-key grouping; the triple collapsing to one cluster; and two genuinely distinct asks that share a subject staying separable
- [x] T044 [US1] Write `agents/consolidator.yaml`: strong tier, merge-or-split within a supplied cluster only, and a schema that contains no `urgency`, `due`, `sources`, or `id` field (contracts/README.md)
- [x] T045 [US1] Implement `src/cos/consolidate/merge.py`: one call per candidate cluster, merge-or-split decision, merged statement, and assembly of the source union **in code** rather than by the model
- [x] T046 [US1] Implement `src/cos/consolidate/rank.py`: pure urgency arithmetic from explicit due-date proximity, configured sender weight, and distinct source count, bounded to 0–100 (R-007)
- [x] T047 [P] [US1] Write `tests/test_rank.py`: the polite-but-empty email ranks below every item with a deadline or a weighted sender; the function is pure and stable across runs; bounds hold at the extremes
- [ ] T048 [US1] Implement `src/cos/brief.py`: render `BRIEF.md` with the ranked list, source links, the `seen_in_runs` staleness marker from D-010, and the run manifest header including which sources were absent
- [ ] T049 [US1] Wire `cos brief` end to end and assert the per-run wall-time budget from SC-014

### Evaluation

- [ ] T050 [US1] Build `tests/golden/`: the expected `TodoItem[]` for the seeded corpus, with each of the six planted traps labelled so a failure names which trap regressed
- [ ] T051 [US1] Write `tests/test_eval_consolidator.py`: a scored evaluation, not equality assertions. Metrics are duplicate recall, false-merge rate, buried-commitment recall, and invented-deadline count. Thresholds fail CI, and the invented-deadline threshold is zero (SC-010)

**Checkpoint**: US1 is independently shippable. A correct ranked brief has value even if no draft is ever written.

---

## Phase 4: User Story 2 — review as a diff (Priority P2)

**Goal**: `cos propose` turns to-dos into editable proposal files on a branch, and opens a
pull request that is a genuine review surface.

**Independent test**: run against a fixed set of to-dos and confirm a branch is pushed and a
pull request opened with one file per action, the brief as its body, and a risk-labelled
table. Merging is not required.

- [ ] T052 [US2] Write `agents/drafter.yaml`: strong tier, one call per actionable to-do, a required `rationale` naming its sources, and the risk rubric where money, external commitments, and declines are `high`
- [ ] T053 [US2] Implement `src/cos/draft/voice.py`: load and render `config/voice.md` into the drafter prompt
- [ ] T054 [US2] Implement `src/cos/draft/drafter.py`: one call per to-do whose `suggested_action` is not `no_action`, skipping anything flagged `needs_human_judgment` (FR-018), and enforcing `max_actions_per_run` as a hard stop that is recorded rather than silent (FR-028)
- [ ] T055 [P] [US2] Write `tests/test_drafter.py`: no draft is produced for `no_action` or for a human-judgement item; the per-run maximum stops the run and is reported; every draft carries a rationale
- [ ] T056 [US2] Implement `src/cos/outbox/writer.py`: serialise a `ProposedAction` to `outbox/pending/<action_id>.md` exactly per contracts/proposal-file.md, with a typed `target` block
- [ ] T057 [US2] Implement `src/cos/outbox/reader.py`: parse a proposal file back to a `ProposedAction`, taking the message body from below the frontmatter, so a human edit is authoritative by construction
- [ ] T058 [P] [US2] Write `tests/test_proposal_roundtrip.py`: write then read yields an equal object; a hand-edited body survives the round trip; a hand-edited recipient is visible to the reader
- [ ] T059 [US2] Implement `src/cos/outbox/pr.py`: branch `cos/run-YYYYMMDD-HHMM`, commit the pending files and `BRIEF.md`, push, and open a pull request whose body is the brief plus a risk-labelled action table with the run manifest header
- [ ] T060 [US2] Add the `high-risk` label when any action is high risk (FR-026), and suppress pull request creation entirely when a run produced no actions (FR-027)
- [ ] T061 [US2] Wire `cos propose` end to end, and add `scripts/run_pipeline.py` as the thin wrapper the workflow calls
- [ ] T062 [US2] Write `.github/workflows/brief.yml` per contracts/workflows.md: scheduled and manual, OIDC login with no stored secret, mail and calendar only (R-012), and a brief that states chat was absent

**Checkpoint**: proposals are reviewable and editable in GitHub. Nothing can send yet.

---

## Phase 5: User Story 3 — merge to send, behind two gates (Priority P3)

**Goal**: a merge plus an environment approval performs the actions, exactly once, only to
allowlisted recipients, with a receipt for each.

**Independent test**: merge a pull request containing one low-risk action to an allowlisted
recipient, approve the environment, and confirm arrival, the file move to `sent/`, and the
ledger entry.

**⚠️ This is the most dangerous code in the repository.** It comes last and behind the most
controls, and the controls live in `src/cos/outbox/executor.py` rather than in workflow YAML,
so a local run cannot bypass them.

- [ ] T063 [US3] Implement `src/cos/outbox/ledger.py`: read `state/ledger.json`, `check_and_reserve(action_id)` as the sole path to a send, and receipt recording with the provider's returned identifier
- [ ] T064 [US3] Implement the allowlist check in `src/cos/outbox/allowlist.py`, evaluated against the file's **current** `target` at execution time so a human edit is still checked (FR-033), failing before any provider call
- [ ] T065 [P] [US3] Write `tests/test_allowlist.py`: an unlisted recipient fails without a provider call; a recipient edited into a file after proposal is still checked; domain and exact-address entries both match correctly
- [ ] T066 [US3] Implement `src/cos/outbox/executor.py`: ordered per contracts/workflows.md — read, allowlist, ledger, per-run maximum, dry-run, perform, receipt. Under `dry_run` it must refuse to construct a live transport at all, rather than merely skipping the call
- [ ] T067 [US3] Implement the Graph write paths: `sendMail`, `createReply` then send, `POST /me/events`, `POST /chats/{id}/messages`, and the GitHub API path for `create_issue`, each returning the provider identifier for the receipt
- [ ] T068 [US3] Implement the outcome paths: success moves the file to `outbox/sent/` with `status`, `sent_at`, and `receipt_id`; failure moves it to `outbox/failed/` with the error, opens an issue, and lets the remaining actions continue (FR-035, FR-036)
- [ ] T069 [US3] Write `tests/test_idempotency.py`: run the executor twice over the same pending file and assert exactly one send (SC-006). This test is the reason the ledger exists
- [ ] T070 [P] [US3] Write `tests/test_dry_run.py`: under `dry_run`, every action is logged in full and no transport is constructed
- [ ] T071 [US3] Wire `cos execute` to the same executor, so rehearsal and production traverse identical controls
- [ ] T072 [US3] Write `.github/workflows/execute.yml`: `push` to `main` path-filtered to `outbox/pending/**`, bound to the `send` environment, committing the ledger back with `[skip ci]` so recording a send cannot trigger sending
- [ ] T073 [US3] Write `tests/test_no_send_paths.py`: assert by import-graph inspection that no module outside `cos.outbox.executor` imports a Graph write function (Constitution I, mechanically)

**Checkpoint**: the full definition of done in the build spec §13 is reachable.

---

## Phase 6: Polish and demo scaffolding

- [ ] T074 [P] Write `scripts/seed_demo_inbox.py`: send real mail and post real chat from alt accounts, planting all six traps from spec.md. Kept outside `src/` because it is scaffolding rather than product
- [x] T075 [P] Write `README.md`: what it is, the architecture diagram, the quickstart from quickstart.md, and the two gates stated plainly
- [ ] T076 [P] Fill `config/voice.md` with real writing rules and three to five genuine sample messages, so drafts sound like the operator
- [ ] T077 Populate `config/allowed_recipients.yaml` with the demo tenant addresses only, and verify the file reads clearly enough to be shown on a projector
- [ ] T078 Run the full pipeline against the live demo tenant end to end, confirming every item in the build spec §13 definition of done
- [ ] T079 [P] Write `docs/live-prompts.md` and `docs/run-of-show.md` — presentation artifacts, deliberately outside the Spec Kit tree
- [ ] T080 Cut and freeze the five checkpoint branches, verifying each from a clean clone. **After** the definition of done is green, never before — they are carved out of finished code, not a build strategy

---

## Dependencies

```
Phase 1 Setup
      │
Phase 2 Foundational  ← blocking; nothing starts before this is green
      │
Phase 3 US1 (P1) ─────────► shippable MVP
      │
Phase 4 US2 (P2) ─────────► reviewable proposals, still cannot send
      │
Phase 5 US3 (P3) ─────────► execution, behind both gates
      │
Phase 6 Polish
```

Story dependencies are real rather than incidental: US2 consumes `TodoItem[]` from US1, and
US3 consumes proposal files from US2. Each is independently *testable* — US2 can be driven
from fixed to-dos and US3 from a hand-written proposal file — but not independently
*deliverable*, because a draft with nothing to draft about has no value.

**B-001 is deliberately off the critical path.** Only T029 (fixture capture), T077, and T078
require a live tenant. Everything else runs against fixtures, so the build proceeds at full
speed while the tenant question is resolved.

## Parallel execution

**Phase 1**: T003 through T008 are all independent files.

**Phase 2**: T011, T012, T017, and T020 are independent test files. T009 must land first,
since everything imports the models.

**Phase 3**: the three source normalisers T024, T025, T026 touch different files. The four
agent YAMLs T032 through T035 are independent. Within consolidation, T041 must precede T042,
and T042 must precede T043 — but T046 and T047 can proceed alongside them, since ranking does
not depend on clustering.

**Phase 4**: T055 and T058 are independent test files.

**Phase 5**: T065 and T070 are independent. T063 must precede T066, and T066 must precede
T069.

## Implementation strategy

Ship US1 first and completely. A correct, deduplicated, fully-sourced brief is the product;
everything after it is delivery mechanism. If the schedule compresses, US1 with a hand-run
`cos propose` is a better outcome than three half-built stories.

Build the deterministic layers before the model-dependent ones within each story — T042
before T045, T046 before T048. It is the cheaper direction to debug, and it is the direction
that keeps Principle III honest: it is much harder to let a model do the clustering when the
clustering already works.
