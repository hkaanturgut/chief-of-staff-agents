# Proposal file contract — `outbox/pending/<action_id>.md`

The human review surface. YAML frontmatter for the machine, markdown body for the person.

```markdown
---
id: 01JX8ZQ4K7N2M5P8R3T6V9W1XY      # ULID, content-derived. The idempotency key.
todo_id: 01JX7YP3J6M1L4N7Q2S5U8V0WX
kind: reply_mail                     # send_mail | reply_mail | create_event | post_chat | create_issue
risk: medium                         # low | medium | high
status: pending                      # pending | sent | failed
generated_at: 2026-08-29T13:02:11Z
run_id: 20260829-1302
model: gpt-5.5
model_version: "2026-04-24"
target:
  to: ["priya@demo.example"]
  cc: []
  subject: "Re: Q3 vendor renewal"
  in_reply_to: "AAMkAGI2..."
rationale: >
  Priya asked twice — Tuesday by mail and again in chat on Wednesday — and the
  invite body for Thursday repeats it. Replying once closes all three.
sources:
  - kind: mail
    id: "AAMk..."
    thread_id: "AAQk..."
    permalink: "https://outlook.office.com/..."
    author: "Priya Raman"
    timestamp: "2026-08-25T09:14:00Z"
    excerpt: "Can you confirm the renewal number before I send it upstream?"
  - kind: chat
    id: "1724..."
    permalink: "https://teams.microsoft.com/l/message/..."
    author: "Priya Raman"
    timestamp: "2026-08-26T14:02:00Z"
    excerpt: "any luck on that renewal figure?"
---

<!-- Everything below the frontmatter is the message body. Edit it freely; your
     changes are what gets sent. -->

Hi Priya,

...
```

## Rules

1. **The body below the frontmatter is what gets sent.** Not `rationale`, not any cached
   copy. A human edit in the GitHub web editor is authoritative by construction, which is
   the entire point of using a diff as the review surface.
2. **`target` is re-read at execution time and re-checked against the allowlist** (FR-033).
   Editing a recipient in the web editor does not slip past the check.
3. **`id` is never regenerated.** It is the ledger key. A file whose `id` changed would
   re-send.
4. **Filename is `<id>.md`.** The queue is self-indexing and a duplicate is a filesystem
   collision rather than a logic error.
5. **`status` is advisory in `pending/`.** The directory the file sits in is the truth; the
   field exists so the file still reads correctly after it has been moved to `sent/`.
6. On success the file moves to `outbox/sent/` with `status: sent` and two appended keys,
   `sent_at` and `receipt_id`. On failure it moves to `outbox/failed/` with `status: failed`,
   `failed_at`, and `error`.
