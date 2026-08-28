<!--
Sync Impact Report
Version change: 0.0.0 (template) → 1.0.0
Bump rationale: MAJOR — initial ratification. All principles newly defined.
Modified principles: none (initial adoption)
Added sections:
  - Core Principles I–VIII
  - Security and Data Handling
  - Development Workflow and Quality Gates
  - Governance
Removed sections: none
Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check gate reviewed; principles
     are expressible as plan gates without template edits.
  ✅ .specify/templates/spec-template.md — scope/requirements structure compatible.
  ✅ .specify/templates/tasks-template.md — task categories cover provenance, logging,
     and deterministic-test task types.
  ✅ .claude/skills/speckit-*/SKILL.md — reviewed; no outdated agent-specific references.
  ⚠ README.md — pending; will reference Principles I, III, and IV when written.
Follow-up TODOs: none.
-->

# Chief of Staff Agents Constitution

## Core Principles

### I. Two Gates Before Any Send (NON-NEGOTIABLE)

No outbound action — mail, calendar event, chat message, or issue — MAY be performed by
any automated path without BOTH of the following, in order: a pull request merged to
`main` by a human, AND an approval recorded on the protected `send` GitHub environment.
Neither gate alone is sufficient. Code that can send without traversing both gates is a
defect of the highest severity, regardless of test coverage.

Supporting controls are mandatory and MUST NOT be removed to make a demo smoother:
`dry_run` defaults to true, `config/allowed_recipients.yaml` hard-fails execution for any
recipient not on it, and `max_actions_per_run` is a hard stop.

*Rationale:* the entire premise of the system is that agents propose and humans decide.
One gate is a workflow. Two independent gates, one reviewing content and one authorising
the act, is a control.

### II. Idempotency Is a Correctness Property

Every `ProposedAction` carries a ULID that is its idempotency key. The executor MUST
consult `state/ledger.json` before acting and MUST skip any id already recorded. Re-running
the executor over the same input MUST produce exactly one send. `TodoItem.id` MUST be
derived deterministically from the merged statement plus sorted source ids, so that an
unchanged inbox produces an unchanged diff.

*Rationale:* a workflow that can be re-triggered by a retry, a re-run, or a second push is
a workflow that will double-send. The ledger check is the only thing standing between the
system and sending the same apology twice.

### III. Determinism Over Inference (NON-NEGOTIABLE)

Where a deterministic implementation and a model call would both work, the deterministic
implementation MUST be used. Specifically: clustering candidate duplicates is done by
normalised thread and conversation id, normalised subject, and regex-extracted entity keys
— never by a language model. Only Stage A's candidate clusters reach Stage B's model, and
Stage B decides merge-or-split within a cluster; it does not discover clusters.

Urgency is computed in code from explicit deadline, configured sender importance, and
distinct source count. The model supplies `urgency_reason` as prose; code supplies the
number.

Any plan, task, or implementation that moves clustering or ranking into a model call
violates this principle and MUST be rejected rather than accommodated.

*Rationale:* do not pay a language model to do what a hash can do. Deterministic code is
cheaper, faster, reviewable, stable across runs, and — decisively — testable.

### IV. Provenance or It Does Not Exist

Every `Signal`, every `TodoItem`, and every `ProposedAction` MUST carry `source_ref`
entries identifying the originating message: kind, id, thread id, permalink, author, and
timestamp. No item MAY enter the pipeline without provenance. When a consolidation merges
several Signals, the merged item MUST retain the source refs of all of them, not a
representative sample.

Deadlines are recorded only when explicitly stated in the source. Inferring a due date
from tone, urgency, or phrasing such as "soon-ish" is forbidden; the correct output is a
null `due` and an ambiguity flag.

*Rationale:* a to-do a human cannot trace back to the message that caused it is a claim,
not a finding. And an invented deadline is worse than a missed one, because it is acted on.

### V. Contracts Are Validated, Never Coerced

All inter-stage data is defined as Pydantic v2 models in `src/cos/models.py`. Every agent
returns structured output validated against them. On a validation failure the call is
retried exactly once with the validation error appended to the prompt; a second failure
fails the run loudly and visibly. Silent coercion, field dropping, and `try/except: pass`
around parsing are forbidden.

*Rationale:* a pipeline that quietly repairs malformed model output hides the exact signal
that tells you the prompt is wrong.

### VI. Tests Are Deterministic; Production Is Live

Live Microsoft Graph at run time, recorded fixtures in CI. `scripts/record_fixtures.py`
captures real Graph responses once and redacts them into `tests/fixtures/`. CI MUST NOT
require a tenant, a token, or a network path to Graph.

The deterministic pre-pass MUST hold the majority of the test suite, tested exactly. Model-
dependent behaviour is covered by a scored evaluation against `tests/golden/` — not
equality assertions — measuring dedup recall, false-merge rate, buried-commitment recall,
and invented-deadline count, the last of which MUST be zero. Thresholds failing means CI
fails. Executor idempotency has its own test that runs the executor twice and asserts one
send.

*Rationale:* "it is non-deterministic so we cannot test it" is how untested systems get
shipped. The answer is to shrink the non-deterministic surface and score what remains.

### VII. The Repository Is the Source of Truth

Agent definitions live as YAML under `agents/` and reach Azure AI Foundry only through
`scripts/provision_agents.py`. Editing an agent in the Foundry portal is forbidden outside
of a deliberate, narrated demonstration, and any such edit MUST be reverted by re-running
provisioning. Infrastructure is declared in `infra/` as Bicep composed from Azure Verified
Modules and MUST be deployable from a clean clone.

The proposal queue is likewise repository state: `outbox/pending`, `outbox/sent`,
`outbox/failed`, and `state/ledger.json` are the system's memory. There is no external
database.

*Rationale:* if the running configuration can drift from the committed configuration, the
committed configuration is documentation rather than truth — and the audit trail the
pull-request gate provides is worthless.

### VIII. Observable by Default

Every model call MUST be logged to `state/runs/` with prompt hash, model name and pinned
version, input and output token counts, and latency. Every run MUST emit a manifest — window
queried, sources used, item counts, total token spend, wall time — which becomes the header
of the pull request description. Logs are structured (`structlog`), not printed prose.

*Rationale:* the cost and latency of an agent pipeline are properties reviewers need in
front of them at review time, in the same artifact they are reviewing.

## Security and Data Handling

The repository is public. It MUST contain no client secret, API key, connection string, or
token, in any file, at any time, including test fixtures and example configuration.

- CI authenticates to Azure through GitHub OIDC federated credentials. No stored secret.
- Local authentication is MSAL device code with a cached token, obtained interactively.
- The Foundry account has `disableLocalAuth: true`; Entra RBAC is the only data-plane path,
  for operators and for CI alike, and role assignments are scoped to the resource, never
  the subscription.
- Subscription, tenant, and client identifiers are Actions **variables** and `.env` keys.
  They are identifiers, not credentials. `.env.example` carries key names with empty values.
- Microsoft Graph application permissions MUST be constrained by an Exchange Application
  Access Policy limited to the single demo mailbox. Tenant-wide `Mail.Read` for a demo is
  forbidden.
- No real client, employer, or third-party data may pass through the system. The demo
  mailbox is a dedicated tenant seeded with fictional traffic.
- Recorded fixtures MUST be redacted at capture time, not before commit.

## Development Workflow and Quality Gates

- `main` is protected: pull request required, force-push and deletion blocked. The `send`
  environment carries a required reviewer and is the authorisation gate for execution.
- Every pull request runs `ci.yml`: contract tests, pre-pass unit tests, idempotency test,
  and the consolidator evaluation.
- Work is specified before it is planned, planned before it is decomposed, and decomposed
  before it is implemented, using the Spec Kit artifacts in `.specify/`. Where the Spec Kit
  decomposition and a hand-written build order disagree, prefer Spec Kit's decomposition
  but preserve the ordering constraint that nothing depends on an unbuilt layer.
- Model versions are pinned with `NoAutoUpgrade` and the choice is recorded with its
  reasoning in `docs/decisions.md`. Behaviour MUST NOT change between rehearsal and
  performance because a model refreshed underneath.
- Known platform limitations are documented plainly rather than worked around silently.
  Where the platform refuses — Teams chat under application permissions requires protected
  API approval — the constraint is stated and the reduced scope is explicit.

## Governance

This constitution supersedes other practices and conventions in this repository. Where a
plan, task list, or review comment conflicts with it, the constitution wins and the
conflicting artifact is amended.

**Amendment procedure.** Amendments are made by pull request that modifies this file,
states the version bump and its rationale, and updates the Sync Impact Report at the top.
Dependent templates under `.specify/templates/` and any affected guidance documents are
updated in the same pull request.

**Versioning policy.** Semantic versioning applies to this document. MAJOR for a removed or
redefined principle, or any change that makes previously compliant work non-compliant.
MINOR for a new principle or materially expanded guidance. PATCH for clarification and
wording.

**Compliance review.** Every pull request is reviewed against these principles, with
particular attention to Principles I, II, and III, whose violations are not visible in
ordinary test output. Complexity that departs from a principle MUST be justified in the
pull request description or removed. Principles marked NON-NEGOTIABLE are not subject to
case-by-case exception.

**Runtime guidance.** `docs/decisions.md` records concrete choices and open blockers.
`docs/spec-source.md` holds the functional source material. Neither overrides this document.

**Version**: 1.0.0 | **Ratified**: 2026-08-22 | **Last Amended**: 2026-08-22
