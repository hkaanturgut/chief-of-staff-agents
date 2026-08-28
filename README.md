# Chief of Staff

A multi-agent assistant that reads your live Microsoft 365 mail, chat, and calendar,
produces one deduplicated to-do list, drafts the replies and actions needed to clear it,
and then **stops**.

Nothing is sent, booked, or posted until a human merges a pull request *and* approves a
protected environment. Two gates, deliberately.

> The agents propose, git decides, the human merges.

Built for [Cloud Summit Toronto 2026](https://cloudsummit.ca) — *From Prompt to Pipeline:
Building a Multi-Agent System in Azure AI Foundry with GitHub Copilot*.

---

## The problem

The same ask arrives three times. An email on Tuesday. A Teams nudge on Wednesday. A line
buried in a Thursday meeting invite. Point a single prompt at all of it and you get three
to-dos, a missed commitment, and a deadline nobody ever stated.

## The shape

```
   Microsoft Graph          ┌──────────────────────────────┐
   ├─ Mail            ─────▶│  chief-of-staff (orchestr.)  │
   ├─ Chat                  │  Azure AI Foundry            │
   └─ Calendar              │  3 connected ingest agents   │
                            └──────────────┬───────────────┘
                                           │ Signal[]
                            ┌──────────────▼───────────────┐
                            │  consolidator  (code-called) │
                            │  A. hash/regex  → clusters   │
                            │  B. model       → merge/split│
                            └──────────────┬───────────────┘
                                           │ TodoItem[]
                            ┌──────────────▼───────────────┐
                            │  drafter       (code-called) │
                            └──────────────┬───────────────┘
                                           │ ProposedAction[]
                            ┌──────────────▼───────────────┐
                            │  outbox/pending/*.md         │
                            │  → branch → Pull Request     │  ← gate 1: a human merges
                            └──────────────┬───────────────┘
                            ┌──────────────▼───────────────┐
                            │  execute.yml, `send` env     │  ← gate 2: a human approves
                            │  Graph send / create / post  │
                            │  → outbox/sent/ + ledger     │
                            └──────────────────────────────┘
```

### Why it is shaped like that

**The three ingest agents are routed by the model. The rest is routed by code.**

Which sources matter genuinely varies by request — "what needs me today" and "prep me for
the 2pm" want different things — so the ingest agents are Foundry *connected agents* and
the orchestrator decides. But consolidate-then-draft never varies in order, so those are
called from code. Non-determinism there would buy nothing and cost correctness.

Most multi-agent demos let the model route everything, then blame the model when the
pipeline is flaky.

**Inside the consolidator, clustering is a hash, not a model.**

Stage A groups candidate duplicates deterministically: conversation id, normalised subject,
and entity keys pulled out by regex. Only the *candidate clusters* reach Stage B, where a
model decides merge-or-split and writes the merged statement.

> Do not pay a language model to do what a hash can do.

This is not an optimisation, it is the architecture. It is cheaper, it is stable across
runs, and — decisively — it is testable. Ranking works the same way: urgency is arithmetic
over an explicit deadline, a configured sender weight, and a source count. The model writes
only the sentence explaining the number it was handed.

**The approval queue is a pull request.** Engineers already trust diff review, so there is
no new UI to teach, and git supplies the audit trail of what the agent proposed versus what
a human changed for free.

## The console

```bash
uv run cos console
```

A local view of one run: the delegation graph, every model call placed on a real
timeline, the ranked brief, the pending drafts, and both gates with their live state.

The approve button is the part worth reading the code for. It shells out to **your** `gh`
credential — the same call github.com makes when you click Approve on the environment. The
console carries no token of its own, and `tests/test_console.py` fails the build if anyone
ever gives it one. Moving the *interface* somewhere friendlier is safe. Moving the *gate*
inside the agent's reach would not be, so it stays where it is: the Azure token that
permits a send is issued by GitHub only after a human approval, and nothing in the pipeline
can mint one.

## What stops it emailing a real person

Four things, and you can read all of them:

| Control | Where |
|---|---|
| Dry run by default | [`config/settings.yaml`](config/settings.yaml) |
| Recipient allowlist, checked against the file's *current* contents | [`config/allowed_recipients.yaml`](config/allowed_recipients.yaml) |
| Required reviewer on the `send` environment | `.github/workflows/execute.yml` |
| Hard stop at five actions per run | [`config/settings.yaml`](config/settings.yaml) |

Plus an append-only ledger: every action carries a content-derived id, and the executor
refuses to act on an id it has already seen. Re-running sends nothing.

## Quickstart

Full version in [`specs/001-chief-of-staff-pipeline/quickstart.md`](specs/001-chief-of-staff-pipeline/quickstart.md).

```bash
uv sync --frozen
cp .env.example .env          # identifiers only — there are no secrets in this repo

az deployment sub create --location canadaeast \
  --template-file infra/main.bicep --parameters infra/main.bicepparam

uv run cos provision          # agents/*.yaml -> Azure AI Foundry
uv run cos login              # device code, cached
uv run cos brief              # -> BRIEF.md
uv run cos propose            # -> outbox/pending/ + a pull request
```

Tests need no tenant, no token, and no network:

```bash
uv run pytest
```

## How it is built

Specified before planned, planned before decomposed, decomposed before implemented, using
[GitHub Spec Kit](https://github.com/github/spec-kit). The artifacts are in the repository
and so is the reasoning:

- [Constitution](.specify/memory/constitution.md) — eight principles, three non-negotiable
- [Specification](specs/001-chief-of-staff-pipeline/spec.md) — 45 requirements, 15 success criteria
- [Plan](specs/001-chief-of-staff-pipeline/plan.md) and [research](specs/001-chief-of-staff-pipeline/research.md)
- [Data model](specs/001-chief-of-staff-pipeline/data-model.md) and [contracts](specs/001-chief-of-staff-pipeline/contracts/)
- [Tasks](specs/001-chief-of-staff-pipeline/tasks.md) — 80, mirrored as issues
- [Decisions and open blockers](docs/decisions.md) — including the ones that went badly

## Where the platform said no

Reading Teams chat under an *application* identity needs Microsoft's protected API
approval, which takes weeks. So the scheduled run covers mail and calendar only, and any
brief produced without chat says so rather than quietly under-reporting. Chat is read in
attended runs under delegated auth.

That is the honest version. See [`docs/decisions.md`](docs/decisions.md) for the rest,
including the tenant that turned out to have no mailboxes in it.

## Licence

MIT.
