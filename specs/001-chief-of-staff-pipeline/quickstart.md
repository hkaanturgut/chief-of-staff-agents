# Quickstart

Clean clone to first brief. Target is under five minutes, excluding Microsoft 365 tenant
setup, which is a prerequisite rather than part of this (SC-015).

## Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Azure CLI, logged in to the subscription holding the Foundry project
- GitHub CLI, authenticated as the repository owner
- A Microsoft 365 tenant you administer, with a dedicated demo mailbox.
  **If you do not have one, read `docs/decisions.md` B-001 first** — the Visual Studio
  subscription's directory has no mailboxes, and this is the one prerequisite that cannot
  be worked around in code.

## 1. Install

```bash
git clone https://github.com/hkaanturgut/chief-of-staff-agents
cd chief-of-staff-agents
uv sync --frozen
```

## 2. Configure

```bash
cp .env.example .env
```

Fill it in. Every value is an identifier, not a credential — there is no secret to paste.
The Azure values come out of the infrastructure deployment; the Graph values come from your
M365 tenant and are deliberately separate (`docs/decisions.md` D-003).

Then check the two files that decide what the system may do:

- `config/allowed_recipients.yaml` — the kill switch. Only addresses listed here can ever
  receive anything. Read it before every run.
- `config/settings.yaml` — `dry_run: true` by default. Leave it that way until you have
  seen a brief you agree with.

## 3. Provision infrastructure (once)

Composed from Azure Verified Modules: a Foundry account and project, two pinned model
deployments, and a budget alert.

```bash
az deployment sub create \
  --location canadaeast \
  --name cos-infra \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam
```

Idempotent — safe to re-run before a rehearsal.

## 4. Provision the agents (once, and after any change to `agents/*.yaml`)

```bash
uv run cos provision
```

The repository is the source of truth. Editing an agent in the Foundry portal is not a
supported workflow; re-running this reverts any such edit.

## 5. Authenticate to Microsoft Graph

```bash
uv run cos login
```

Device code, once, cached to disk. **Run this within 30 minutes of any demonstration.** An
expired token is the single most likely way a live run fails, and re-authenticating in front
of an audience costs twenty seconds if you have rehearsed it and considerably longer if you
have not.

## 6. Run

```bash
uv run cos brief --raw          # normalised source objects, no agents. Proves auth and retrieval.
uv run cos brief --signals      # extracted signals with provenance, no consolidation.
uv run cos brief                # the full ranked brief -> BRIEF.md
uv run cos propose              # drafts -> outbox/pending/, branch, pull request
```

Then review the pull request. Edit any draft body directly in the web editor; your edit is
what gets sent.

## 7. Execute

Merge the pull request. Nothing has been sent. Go to the Actions tab, find the run waiting
on the `send` environment, and approve it. That approval is what performs the actions.

To rehearse the whole path without sending anything, leave `dry_run: true`: the executor
logs each action exactly as it would perform it, and touches no provider.

## Tests

```bash
uv run pytest
```

No tenant, no token, no network. Everything replays from `tests/fixtures/`.

```bash
uv run pytest tests/test_prepass.py -v         # the deterministic core
uv run pytest tests/test_eval_consolidator.py  # the scored evaluation
```

## Pre-flight, before any live run

1. `az account show` — correct subscription.
2. `uv run cos login` — token fresh.
3. `cat config/allowed_recipients.yaml` — read it, out loud if there is an audience.
4. `cat config/settings.yaml | grep dry_run` — know which mode you are in.
5. `uv run pytest -q` — green.
6. `uv run cos brief --raw` — retrieval works against the live tenant right now.
