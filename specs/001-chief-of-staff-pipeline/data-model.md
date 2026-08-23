# Phase 1: Data Model

Pydantic v2 models in `src/cos/models.py`. These are the wire contracts between stages and
the schemas agents are constrained to. Field-level invariants are expressed as validators,
not as documentation, so a violation is a `ValidationError` rather than a review comment.

`model_config = ConfigDict(extra="forbid", frozen=True)` on every model. Forbidding extras
means an agent inventing a field fails loudly (Principle V); freezing means a stage cannot
quietly mutate an upstream object.

## Boundary models — what the sources produce

These exist so no agent sees raw Graph JSON. They are normalisation targets, not agent
output.

### MailMessage
| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Graph message id |
| `conversation_id` | `str \| None` | Graph `conversationId`; the strongest clustering key |
| `internet_message_id` | `str \| None` | RFC 5322 id, survives across mailboxes |
| `subject` | `str` | Raw, un-normalised. Normalisation happens in the pre-pass. |
| `from_address` / `from_name` | `str` / `str \| None` | |
| `to` / `cc` | `list[str]` | Addresses only |
| `received_at` | `datetime` | Timezone-aware, always. Naive datetimes are rejected. |
| `body_text` | `str` | Plain text. HTML is stripped at normalisation, once. |
| `web_link` | `str \| None` | Deep link into Outlook |
| `is_from_operator` | `bool` | Drives buried-commitment detection |

### ChatMessage
| Field | Type | Notes |
|---|---|---|
| `id`, `chat_id` | `str` | |
| `from_address` / `from_name` | `str \| None` / `str \| None` | Chat allows unresolvable senders |
| `sent_at` | `datetime` | Timezone-aware |
| `body_text` | `str` | |
| `web_link` | `str \| None` | `https://teams.microsoft.com/l/message/...` |
| `is_from_operator` | `bool` | |
| `preceding_context` | `list[str]` | Up to N prior messages. Chat is low-context; the referent for "can you take a look?" usually lives here. Without it the agent has no honest way to resolve the ask, and guessing is forbidden. |

### CalendarEvent
| Field | Type | Notes |
|---|---|---|
| `id`, `subject` | `str` | |
| `start`, `end` | `datetime` | Timezone-aware |
| `organizer` | `str` | |
| `attendees` | `list[str]` | |
| `body_text` | `str` | Invite bodies carry asks — one leg of the triple |
| `is_all_day`, `is_cancelled` | `bool` | |
| `web_link` | `str \| None` | |

## Contract models — the four in the specification

### SourceRef
The unit of provenance. Everything downstream carries a list of these.

| Field | Type | Invariant |
|---|---|---|
| `kind` | `Literal["mail","chat","calendar"]` | |
| `id` | `str` | `min_length=1` |
| `thread_id` | `str \| None` | |
| `permalink` | `str \| None` | |
| `author` | `str` | |
| `timestamp` | `datetime` | Must be timezone-aware |
| `excerpt` | `str` | `max_length=240`. Truncated on the word boundary at normalisation, with an ellipsis — the pull request body must stay readable. |

Equality and hashing are on `(kind, id)`, so merging source lists deduplicates naturally
when the same message is cited by two signals.

### Signal
| Field | Type | Invariant |
|---|---|---|
| `type` | `Literal["ask","commitment","fyi","deadline","meeting_prep","conflict"]` | |
| `statement` | `str` | `min_length=1` |
| `counterparty` | `str \| None` | |
| `due` | `datetime \| None` | **Explicit only.** A model validator rejects a non-null `due` when `ambiguous` is true — a deadline cannot be both uncertain and stated. This is the invented-deadline trap turned into a contract. |
| `ambiguous` | `bool` | Default false |
| `sources` | `list[SourceRef]` | `min_length=1` (Principle IV) |

### TodoItem
| Field | Type | Invariant |
|---|---|---|
| `id` | `str` | 26-char ULID, content-derived. See Identifier derivation. |
| `title` | `str` | `max_length=120` |
| `detail` | `str` | |
| `owner` | `Literal["me","delegate","waiting"]` | |
| `due` | `datetime \| None` | Explicit only, inherited from the merged signals |
| `urgency` | `int` | `ge=0, le=100`. **Computed in code.** |
| `urgency_reason` | `str` | Written by the model. Explains the number; cannot change it. |
| `suggested_action` | `Literal["reply","schedule","delegate","create_issue","no_action"]` | |
| `confidence` | `float` | `ge=0.0, le=1.0` |
| `needs_human_judgment` | `bool` | When true, `suggested_action` must be `no_action` — enforced by a model validator, per FR-018. |
| `sources` | `list[SourceRef]` | `min_length=1`, the union across every merged signal |
| `seen_in_runs` | `int` | Default 1. Consecutive runs this item has survived (D-010). |

### ProposedAction
| Field | Type | Invariant |
|---|---|---|
| `id` | `str` | ULID, content-derived. **This is the idempotency key.** |
| `todo_id` | `str` | |
| `kind` | `Literal["send_mail","reply_mail","create_event","post_chat","create_issue"]` | |
| `risk` | `Literal["low","medium","high"]` | |
| `target` | `MailTarget \| EventTarget \| ChatTarget \| IssueTarget` | Discriminated on `kind`. A plain `dict` was rejected: the executor branches on this, and an untyped payload is exactly where a wrong recipient hides. |
| `body_markdown` | `str` | The human-editable payload |
| `rationale` | `str` | Must name the sources that drove the draft |
| `sources` | `list[SourceRef]` | `min_length=1` |

Target variants: `MailTarget(to, cc, subject, in_reply_to)`; `EventTarget(subject, start, end, attendees, body)`; `ChatTarget(chat_id, chat_topic)`; `IssueTarget(repo, title, labels)`.

## Supporting models

### RunManifest
`run_id`, `started_at`, `finished_at`, `window_start`, `window_end`, `sources_requested`,
`sources_succeeded`, `sources_failed`, counts per stage (`messages_in`, `signals`,
`clusters`, `todos`, `actions`), `model_calls`, `input_tokens`, `output_tokens`,
`estimated_cost_usd`, `wall_seconds`, `dry_run`.

`sources_failed` being non-empty is not an error. It is a fact the brief and the pull
request must state, so a reader is never misled about coverage (FR-009, R-012).

### ModelCallLog
One JSON line per call in `state/runs/<run_id>.jsonl`: `run_id`, `agent`, `model`,
`model_version`, `prompt_hash`, `input_tokens`, `output_tokens`, `latency_ms`, `attempt`,
`validation_error`. Prompts are hashed, not stored — the repository is public and the
window contains mailbox content.

### LedgerEntry
`action_id`, `todo_id`, `kind`, `performed_at`, `receipt_id` (the provider's returned id),
`pr_number`, `run_id`. `action_id` is unique across the file; the executor's
check-and-reserve is the only writer.

## Identifier derivation

Both identifiers are pure functions of content, which is what produces a clean diff on an
unchanged inbox (FR-016, SC-005).

```
todo_id     = ulid_from_hash(blake2b(normalise(statement) || "\x00" || "\x00".join(sorted(f"{s.kind}:{s.id}" for s in sources))))
action_id   = ulid_from_hash(blake2b(todo_id || "\x00" || kind || "\x00" || canonical_json(target)))
```

`ulid_from_hash` takes the first 10 bytes of the digest as the ULID's random component and a
**fixed** timestamp component. A real timestamp would change the identifier every run, which
is precisely the failure being designed out. The result is Crockford base-32, 26 characters,
sorts stably, and is a legal ULID by shape if not by provenance.

`normalise` lowercases, collapses whitespace, and strips trailing punctuation. It does not
stem or lemmatise: two statements differing by a word are different asks, and a merge is the
model's call, not the hash's.

## Invariants worth restating

1. Every `Signal`, `TodoItem`, and `ProposedAction` has at least one `SourceRef`. Enforced by
   `min_length=1`, not by review.
2. A `due` is never inferred. `ambiguous=True` with a non-null `due` is a contract violation.
3. `urgency` is written by `consolidate/rank.py`. No model output path can set it — the merge
   agent's schema does not contain the field.
4. `needs_human_judgment=True` forces `suggested_action="no_action"`, so no draft is produced
   for an item the system does not understand.
5. `extra="forbid"` everywhere. An agent that invents a field fails the run.
6. All datetimes are timezone-aware. A naive datetime is rejected at the boundary, because
   a window comparison against a naive value is a bug that only appears near midnight.
