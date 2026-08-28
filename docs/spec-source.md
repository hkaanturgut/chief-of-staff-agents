# Source material for the specification

Condensed from the build spec, §1 through §6. Stage choreography and the talk timeline
are deliberately absent: they are not product requirements and would pollute the spec.

## What we are building

A multi-agent "Chief of Staff" that reads live Microsoft 365 mail, chat, and calendar,
produces a deduplicated to-do list, drafts the replies and actions needed to clear it,
and then stops. Nothing is sent, booked, or posted until a human approves it as a
GitHub pull request.

The agents propose, git decides, the human merges.

### Hard rules

1. **No autonomous send.** Every outbound action requires a merged PR *and* a
   protected-environment approval. Two gates, deliberately.
2. **No client data on screen.** The demo runs against a dedicated demo mailbox seeded
   with realistic-but-fictional traffic. Live Graph calls, live data, zero
   confidentiality risk.
3. **Deterministic tests.** Live data at demo time, recorded fixtures in CI.
4. **Every checkpoint is a green branch.** Nothing depends on a previous live step.

## Architecture

Microsoft Graph (mail, chat, calendar) feeds a Chief of Staff orchestrator hosted in
Azure AI Foundry with three connected ingest agents. Their `Signal[]` output goes to a
consolidator called from code, which emits `TodoItem[]`. Those go to a drafter, also
called from code, which emits `ProposedAction[]`. Those are written to
`outbox/pending/*.md`, committed to a branch, and opened as a pull request. A human
merges. A gated workflow then performs the Graph writes and records receipts in a
ledger.

**The routing rule.** The three ingest agents are Foundry *connected agents* under the
orchestrator, because which sources are relevant genuinely varies by request and letting
the model route is correct. The consolidator and drafter are called from *code*, in a
fixed order, because their order never varies and non-determinism there buys nothing and
costs correctness.

## Agents

Six. Agent definitions live as YAML in `agents/*.yaml` and are provisioned to Foundry by
`scripts/provision_agents.py`. The repository is the source of truth; never hand-edit in
the portal.

1. **`mail-triage`** (Foundry connected, cheap tier). Input: normalised `MailMessage[]`
   for a window, default last 24h. Extracts `Signal` objects only — no summarising, no
   prioritising, no drafting, enforced in both instructions and output schema. Every
   Signal carries a `source_ref`; nothing enters the pipeline without provenance.
   Threaded mail repeats context, so an ask is extracted once at the point it was first
   made, with later restatements recorded as additional `source_ref` entries rather than
   new Signals.

2. **`chat-triage`** (Foundry connected, cheap tier). Input: normalised `ChatMessage[]`.
   A separate agent because chat has a different failure mode: low context, high noise,
   implicit asks with no stated object. Must resolve the referent from surrounding
   messages or mark the Signal `ambiguous: true` rather than guess. Must never invent a
   deadline — `due` stays null unless explicitly stated.

3. **`calendar-context`** (Foundry connected, cheap tier). Input: `CalendarEvent[]` for
   today plus the next two business days. Emits `meeting_prep` and `conflict` Signals,
   and supplies the shape of the day so ranking is realistic: a to-do needing two hours
   on a day holding six hours of meetings should be flagged.

4. **`consolidator`** (called from code, strong tier). Two stages, and the split is the
   point.
   - *Stage A, deterministic pre-pass, plain Python, no LLM:* group by normalised thread
     and conversation id; by normalised subject with `Re:`/`Fwd:`, punctuation, and case
     stripped; and by shared entity keys — ticket ids, PR urls, doc urls, invoice
     numbers — extracted by regex. Optionally use embedding cosine similarity above a
     threshold, as a candidate-pair generator only.
   - *Stage B, LLM merge:* only Stage A's candidate clusters reach the model, which
     decides merge or split and writes the merged statement.

   **This split is a hard architectural constraint, not an optimisation.** If a plan
   lets a language model do the clustering, the plan is wrong. Do not pay a language
   model to do what a hash can do.

   Ranking: urgency is computed in code from explicit deadline, sender importance drawn
   from a configured allowlist rather than vibes, and the number of distinct sources.
   The model supplies the reason string; code supplies the number. Reviewable, and
   stable across runs.

5. **`drafter`** (called from code, strong tier). One call per `TodoItem` whose
   `suggested_action` is not `no_action`. Produces a `ProposedAction`; never sends.
   Loads `config/voice.md` — writing rules plus three to five real sent-mail samples —
   so drafts sound like the author. Must produce a `rationale` naming which sources drove
   the draft, and set `risk`. Anything touching money, commitments to external parties,
   or a decline is `high` and gets a louder label in the PR.

6. **`chief-of-staff`** (Foundry orchestrator). Owns the three connected ingest agents,
   decides which sources to pull for a given request, and emits a run manifest — window
   queried, sources used, counts, token spend, wall time — which becomes the PR
   description header.

## Data contracts

Pydantic v2 models in `src/cos/models.py`. Every agent returns structured output
validated against them. A validation failure retries once with the error appended, then
fails the run loudly. No silent coercion.

- `SourceRef`: `kind` (mail|chat|calendar), `id`, `thread_id`, `permalink` (a deep link
  back into Outlook or Teams), `author`, `timestamp`, `excerpt` (≤240 chars, for the PR
  body).
- `Signal`: `type` (ask|commitment|fyi|deadline|meeting_prep|conflict), `statement`,
  `counterparty`, `due` (explicit only, never inferred), `ambiguous`, `sources`.
- `TodoItem`: `id` (ULID, stable across runs via content hash), `title`, `detail`,
  `owner` (me|delegate|waiting), `due`, `urgency` 0-100 computed in code,
  `urgency_reason` written by the model, `suggested_action`
  (reply|schedule|delegate|create_issue|no_action), `confidence`,
  `needs_human_judgment`, `sources`.
- `ProposedAction`: `id` (ULID, the idempotency key), `todo_id`, `kind`
  (send_mail|reply_mail|create_event|post_chat|create_issue), `risk` (low|medium|high),
  `target`, `body_markdown` (the human-editable payload), `rationale`, `sources`.

**Stable ids matter.** `TodoItem.id` hashes the merged statement plus sorted source ids,
so re-running against an unchanged inbox produces the same ids and the PR diff stays
clean.

## The approval mechanism

**Proposal artifact.** One file per action at `outbox/pending/<ulid>.md`: YAML
frontmatter carrying id, todo_id, kind, risk, status, target fields, `generated_at`,
model, and sources; then the message body as plain markdown below it. Frontmatter plus
body means editing in the GitHub web editor is natural and a human edit produces a clean
diff.

**Run to PR.** `scripts/run_pipeline.py`, also `.github/workflows/brief.yml`: resolve the
window, call Graph, normalise; run orchestrator, consolidator, drafter; write `BRIEF.md`
at the repository root holding the ranked to-do list with source links and the run
manifest; write each `ProposedAction` to `outbox/pending/`; branch
`cos/run-YYYYMMDD-HHMM`, commit, push; open a PR titled
`Chief of Staff: N to-dos, M proposed actions (date)` whose body is `BRIEF.md` plus a
checklist table of actions with risk labels; label it `high-risk` if any action is
high risk; request review.

**Merge to execute.** `.github/workflows/execute.yml`, on push to `main`, path-filtered
to `outbox/pending/**`. The job runs in the GitHub environment `send` with a required
reviewer — this is the second gate; merging stages the send, approving the environment
fires it. For each changed pending file, check `state/ledger.json` for the ULID and skip
if present. **Idempotency is non-negotiable: a re-run must never double-send.** Execute
via Graph (`sendMail`, `createReply` then send, `POST /me/events`,
`POST /chats/{id}/messages`) or the GitHub API for `create_issue`. On success move the
file to `outbox/sent/`, set `status: sent`, append the Graph message id as a receipt,
append to the ledger, and commit back to `main` with `[skip ci]`. On failure move to
`outbox/failed/` with the error and open an issue.

**Kill switches.** `dry_run: true` by default in `config/settings.yaml`.
`config/allowed_recipients.yaml` is an allowlist; execution hard-fails on anything not
on it. The environment protection rule. And `max_actions_per_run: 5`, a hard stop.

## Live data and auth

**Demo tenant, not the day job.** A dedicated M365 demo tenant or a separate demo mailbox,
seeded 24 to 48 hours ahead with realistic traffic from alt accounts. Still live Graph,
live tokens, live API calls. What is controlled is the content.

**Scopes.** Delegated, for local and stage runs: `User.Read`, `Mail.Read`, `Mail.Send`,
`Mail.ReadWrite`, `Calendars.ReadWrite`, `Chat.Read`, `ChatMessage.Send`. Application, for
the scheduled CI run: `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite` — scoped by an
Exchange Application Access Policy limited to the single demo mailbox. Granting a
tenant-wide app `Mail.Read` for a conference demo is exactly what this talk should not
model.

**Known blocker: Teams chat in CI.** Application-permission access to Teams chat messages
requires Microsoft's protected API approval, which takes days to weeks. The scheduled CI
run therefore reads mail and calendar only; Teams chat runs locally under delegated auth.
This is a constraint to state plainly, not to paper over.

**Token handling.** Locally, MSAL with a persistent token cache; `scripts/auth_login.py`
does device code once and caches. In CI, `azure/login` with a federated credential over
OIDC, no stored secrets.

**Graph client notes.** Graph SDK or plain httpx against v1.0; no beta endpoints. Delta
queries for mail so repeat runs are fast. Retry with backoff on 429. Normalise at the
boundary — `src/cos/sources/{mail,chat,calendar}.py` return internal models, so agents
never see raw Graph JSON, prompts stay small, and fixtures swap in for free.

## Testing and evals

Live data at demo time does not excuse untestable code.

- `scripts/record_fixtures.py` captures real Graph responses once, redacts them, and
  stores them under `tests/fixtures/`. CI replays these, so tests are deterministic while
  production is live.
- `tests/test_prepass.py` covers the deterministic dedup path with no model in the loop.
  Fast, exact, and it should be the majority of the suite. The more work pushed into
  deterministic code, the more of the system can actually be tested.
- `tests/test_eval_consolidator.py` is a scored eval against `tests/golden/`, not an
  equality assert. Metrics: dedup recall, false-merge rate, buried-commitment recall, and
  invented-deadline count, which must be zero. Below threshold fails CI.
- `tests/test_idempotency.py` runs the executor twice on the same pending file and
  asserts exactly one send.
- Every model call is logged with prompt hash, model version, tokens, and latency to
  `state/runs/`. Cost per run goes in the PR description.

## Planted traps the system must handle

The demo mailbox is seeded with these on purpose; they are the acceptance criteria.

1. **The triple.** One ask arrives three ways — an email, a Teams nudge, and a line in a
   meeting invite body. A single agent yields three to-dos. The consolidator yields one
   with three sources.
2. **The buried commitment.** Deep in a long thread, the user wrote "I'll send the revised
   numbers by Friday." A naive run misses it.
3. **The invented deadline.** A chat message says "soon-ish". Correct behaviour is
   `due: null` and a flag, never a fabricated Friday.
4. **The polite trap.** An email that reads urgent but requires nothing. It must rank low,
   proving urgency is computed rather than vibed.
5. **A high-risk action.** Something involving money or an external decline, so the
   `risk: high` label and the louder PR treatment appear.
6. **Volume.** 25 to 40 messages, enough that manual triage is visibly tedious.

## Stack

Python 3.11+ with `uv`. Azure AI Foundry for models and hosted agents. Microsoft Agent
Framework (`agent-framework`) for orchestration and `azure-ai-projects` for agent
provisioning and connected agents. `azure-identity` plus MSAL for the delegated path.
`typer` for the CLI, `structlog` for structured logs, `pydantic` v2 for contracts,
`pytest` for tests. Model versions pinned in `config/settings.yaml`, with the reason
recorded.
