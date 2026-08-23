# Workflow contract

Three workflows. The gates live here, and nowhere else may perform an outbound action.

## `ci.yml`

**Trigger**: `pull_request`, and `push` to `main`.
**Permissions**: `contents: read`.
**Credentials**: none. No Azure login, no Graph token.
**Does**: `uv sync --frozen`, then `pytest`. Contract tests, pre-pass, ranking, identifier
stability, allowlist, idempotency, import-graph, and the scored consolidator evaluation
against committed fixtures and golden files.

Requires no tenant and no network path to Microsoft Graph (SC-009). Advisory rather than
merge-blocking, per `docs/decisions.md` D-005.

## `brief.yml`

**Trigger**: `schedule` (weekday mornings) and `workflow_dispatch` with optional window
override.
**Permissions**: `contents: write`, `pull-requests: write`, `id-token: write`.
**Credentials**: `azure/login` with the OIDC federated credential — no stored secret.
**Sources**: mail and calendar only. Chat is excluded from unattended runs (R-012), and the
brief states that it was.

**Does**: runs the pipeline, writes `BRIEF.md` and the pending files, pushes branch
`cos/run-YYYYMMDD-HHMM`, and opens a pull request whose body is the brief plus a
risk-labelled action table. Labels the pull request `high-risk` if any action is high risk.
Opens nothing when the run produced no actions (FR-027).

**Never sends.** This workflow has no path to an outbound action.

## `execute.yml` — the second gate

**Trigger**: `push` to `main`, path-filtered to `outbox/pending/**`.
**Environment**: `send` — required reviewer, so the job halts until a human approves.
**Permissions**: `contents: write`, `issues: write`, `id-token: write`.

**Does**, per changed pending file, in this order:

1. Read the file and parse `target` from its **current** contents.
2. Check `target` against `config/allowed_recipients.yaml`. A miss fails the action before
   any provider call (FR-032, FR-033).
3. Check `id` against `state/ledger.json`. Present means skip, silently and successfully —
   a re-run is expected, not exceptional (FR-030).
4. Stop entirely if `max_actions_per_run` is reached.
5. If `dry_run`, log the action fully and perform nothing (FR-034).
6. Otherwise perform it through Graph or the GitHub API, capture the returned identifier.
7. Move the file to `sent/`, append the ledger entry, commit to `main` with `[skip ci]`.
8. On failure, move to `failed/` with the error, open an issue, and continue with the
   remaining actions (FR-035, FR-036).

**The two gates.** Merging the pull request stages the send. Approving the `send`
environment fires it. Neither alone is sufficient, and no other workflow, script, or CLI
path may perform an outbound action (Constitution I).

`cos execute` exists locally for rehearsal and honours every one of the same controls,
because the controls live in `src/cos/outbox/executor.py` rather than in workflow YAML. A
control implemented in a workflow is a control that a local run bypasses.
