# Contracts

Four kinds of contract, all of which are checkable.

| File | What it binds |
|---|---|
| `signal-extraction.schema.json` | Output of `mail-triage`, `chat-triage`, `calendar-context`. Constrains the model at call time and is validated again in process. |
| `cluster-merge.schema.json` | Output of the consolidator's Stage B, per candidate cluster. Deliberately narrow: merge-or-split and a statement. It cannot set urgency. |
| `proposed-action.schema.json` | Output of the drafter, one per actionable to-do. |
| `proposal-file.md` | The on-disk format of `outbox/pending/*.md`. The human review surface. |
| `ledger.schema.json` | `state/ledger.json`. The authority on what has already been sent. |
| `workflows.md` | The trigger, permission, and gate contract of the two pipeline workflows. |

Schemas are generated from the Pydantic models by `scripts/export_schemas.py` and committed.
A test asserts the committed files match what the models currently produce, so a model change
that is not reflected here fails CI rather than drifting silently.

## Why the merge schema is narrow

`cluster-merge.schema.json` contains `merge: bool`, `statement: str`, `title: str`,
`detail: str`, `owner`, `suggested_action`, `confidence`, `needs_human_judgment`,
`urgency_reason`, and — when `merge` is false — a `split_groups` array partitioning the
input signal indices.

It does **not** contain `urgency`, `due`, `sources`, or `id`.

- `urgency` is computed by `consolidate/rank.py` (Principle III).
- `due` is carried forward from the merged signals, which took it from an explicit statement
  in a source. Letting the merge step restate a date is how an inferred deadline would sneak
  back in through the side door.
- `sources` is the union of the input signals' sources, assembled in code. A model asked to
  copy source lists will eventually drop one, and Principle IV is not a best-effort.
- `id` is derived from content after the merge.

The model decides what is one ask and how to say it. Everything mechanical is mechanical.
