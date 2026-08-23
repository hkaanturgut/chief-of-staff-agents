# Feature Specification: Chief of Staff Pipeline

**Feature Branch**: `001-chief-of-staff-pipeline`

**Created**: 2026-08-22

**Status**: Draft

**Input**: `docs/spec-source.md` — the functional source material, §1 through §6 of the
build spec. Stage choreography and talk timing are deliberately excluded.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See one honest to-do list instead of three tools (Priority: P1)

On a Monday morning the operator has mail, Teams chat, and a calendar, and the same ask
has arrived through all three. They want one ranked list of what actually needs them
today, where each item names the messages it came from and nothing has been invented.

**Why this priority**: this is the whole product. Every later story consumes this output,
and without it there is nothing to approve or execute. It also delivers standalone value:
a correct ranked brief is useful even if no draft is ever written.

**Independent Test**: run the brief against a mailbox seeded with the six planted traps
and read `BRIEF.md`. The triple appears once with three sources; the buried commitment
appears; the "soon-ish" message has no due date; the polite-but-empty email ranks low.
No later stage is required.

**Acceptance Scenarios**:

1. **Given** an ask that arrived as an email, a chat message, and a line in a meeting
   invite body, **When** the brief runs, **Then** exactly one to-do is produced carrying
   all three source references.
2. **Given** a long thread in which the operator wrote "I'll send the revised numbers by
   Friday", **When** the brief runs, **Then** a commitment-type item is produced with an
   explicit Friday due date and a source reference to that message.
3. **Given** a chat message saying a task is needed "soon-ish", **When** the brief runs,
   **Then** the resulting item has no due date and is flagged as ambiguous.
4. **Given** an email with urgent phrasing that asks for nothing, **When** the brief runs,
   **Then** the item ranks below every item carrying an explicit deadline or an important
   sender, and its urgency reason states why.
5. **Given** an unchanged mailbox, **When** the brief runs a second time, **Then** every
   to-do identifier is identical to the first run.
6. **Given** an ask restated three times inside one email thread, **When** the brief runs,
   **Then** one item is produced with three source references, not three items.

---

### User Story 2 - Review what the assistant wants to send, as a diff (Priority: P2)

The operator wants the proposed replies, meeting invitations, and messages presented the
way they already review work: as files in a pull request they can read, edit in place, and
merge or close.

**Why this priority**: it converts the brief from information into action, and it is the
gate that makes the system safe to point at a real mailbox. It depends on Story 1's output
but nothing depends on it, so it can ship and be demonstrated on its own.

**Independent Test**: run the propose step against a fixed set of to-dos and confirm a
branch is pushed and a pull request opened containing one markdown file per action, a
brief in the description, and a risk-labelled table. Merging is not required to test it.

**Acceptance Scenarios**:

1. **Given** to-dos whose suggested action is not "no action", **When** the propose step
   runs, **Then** one markdown file per action is written to the pending queue, each with
   frontmatter carrying its identifier, kind, risk, targets, and sources, and the editable
   message body below.
2. **Given** any proposed action assessed as high risk, **When** the pull request is
   opened, **Then** it is labelled as high risk and that action is visibly marked in the
   action table.
3. **Given** an open pull request, **When** the operator edits a message body in the web
   editor, **Then** the change appears as an ordinary line-level diff against the
   assistant's original text.
4. **Given** a run producing more than the configured maximum number of actions, **When**
   the propose step runs, **Then** it stops at the maximum and states in the pull request
   that it did so.
5. **Given** a completed run, **When** the pull request is opened, **Then** its description
   carries the run manifest: window queried, sources used, item counts, token spend, and
   wall time.

---

### User Story 3 - Merge to send, with a second human gate (Priority: P3)

Having reviewed and edited, the operator merges. Nothing has been sent yet. A separate
approval, on a protected environment, is what actually releases the messages — and a
receipt is recorded for each one.

**Why this priority**: it is the payoff, but it is also the most dangerous code in the
system, so it comes last and behind the most controls. Stories 1 and 2 are useful without
it; it is useless without them.

**Independent Test**: merge a pull request containing one low-risk action to an allowlisted
recipient, approve the environment, and confirm the message arrives, the file moves to the
sent queue with a receipt, and the ledger records the identifier.

**Acceptance Scenarios**:

1. **Given** a merged pull request containing pending actions, **When** the execution
   workflow triggers, **Then** it halts awaiting approval and sends nothing until a named
   reviewer approves.
2. **Given** an approved execution, **When** each action is performed, **Then** the file
   moves to the sent queue with its status updated and the provider's message identifier
   recorded as a receipt, and the identifier is appended to the ledger.
3. **Given** an action whose identifier is already in the ledger, **When** execution runs
   again, **Then** that action is skipped and nothing is sent.
4. **Given** an action addressed to a recipient absent from the allowlist, **When**
   execution runs, **Then** it fails before contacting the provider and sends nothing.
5. **Given** dry-run mode is enabled, **When** execution runs, **Then** every action is
   logged as it would have been performed and nothing is sent.
6. **Given** an action that fails at the provider, **When** execution runs, **Then** the
   file moves to the failed queue carrying the error, an issue is opened, and the
   remaining actions still execute.

---

### Edge Cases

- **A source is unreachable.** If mail is retrievable but chat is not, the run completes
  over the sources that answered, and the manifest and pull request state which source was
  missing. A partial brief that says it is partial beats no brief.
- **The provider throttles.** Requests back off and retry. If the window still cannot be
  retrieved, the run fails visibly rather than silently briefing on a fraction of it.
- **A model returns output that does not satisfy the contract.** The call is retried once
  with the validation error appended; a second failure ends the run loudly.
- **Two distinct asks look alike.** The deterministic pre-pass may group them as
  candidates; the merge step must split them. A wrong merge is worse than a missed one,
  because it hides an ask.
- **An ask arrives with no identifiable counterparty.** The item is produced, marked as
  needing human judgement, and no draft is proposed for it.
- **The window contains nothing actionable.** A brief is still produced, stating so, and no
  pull request is opened — an empty pull request is noise.
- **The same run is triggered twice.** Identifiers are content-derived, so the second run
  produces the same items and an empty diff rather than duplicates.
- **A pending file is edited to change its recipient.** The allowlist is enforced at
  execution time against the file's contents, not against what was proposed, so an edited
  recipient is still checked.
- **A pending file is merged without ever being reviewed.** The environment approval still
  stands between it and the provider.
- **An item is neither actioned nor dismissed.** It is re-derived on the next run with the
  same identifier, and the brief marks how many consecutive runs it has survived.

## Requirements *(mandatory)*

### Functional Requirements

**Ingestion and provenance**

- **FR-001**: System MUST retrieve mail, chat, and calendar items for a resolved time
  window from the operator's account, using live provider APIs at run time.
- **FR-002**: System MUST normalise every retrieved item into an internal representation
  before any agent sees it, so that no agent consumes raw provider payloads.
- **FR-003**: System MUST attach a source reference — kind, identifier, thread identifier,
  deep link, author, timestamp, and a short excerpt — to every extracted signal.
- **FR-004**: System MUST reject any signal, to-do, or proposed action that carries no
  source reference.
- **FR-005**: System MUST extract signals only, at the ingestion stage: no summarising, no
  prioritising, and no drafting.
- **FR-006**: System MUST record a due date only when one is explicitly stated in a source,
  and MUST leave it unset otherwise.
- **FR-007**: System MUST mark a signal ambiguous when the thing being asked for cannot be
  resolved from surrounding context, rather than guessing.
- **FR-008**: System MUST extract a repeated ask once, at its earliest occurrence, and
  attach later restatements as additional source references.
- **FR-009**: System MUST report, per run, which sources were queried and which returned
  results.

**Consolidation and ranking**

- **FR-010**: System MUST group candidate duplicates deterministically, without a language
  model, using thread and conversation identity, normalised subject, and entity keys
  extracted from text.
- **FR-011**: System MUST pass only candidate groups to the merge step, and MUST NOT allow
  the merge step to discover groupings of its own.
- **FR-012**: System MUST decide merge-or-split within each candidate group and produce a
  single merged statement for each merged item.
- **FR-013**: System MUST carry every source reference from every merged signal onto the
  resulting to-do.
- **FR-014**: System MUST compute an urgency score in code from the explicit due date, the
  configured importance of the sender, and the count of distinct sources.
- **FR-015**: System MUST accompany each urgency score with a written reason, supplied by
  the model, that does not alter the score.
- **FR-016**: System MUST assign each to-do a stable identifier derived from its merged
  statement and its sorted source identifiers, such that an unchanged input yields an
  unchanged identifier.
- **FR-017**: System MUST classify each to-do by owner — the operator, a delegate, or
  awaiting someone else — and by suggested action.
- **FR-018**: System MUST flag to-dos that require human judgement and MUST NOT propose an
  action for them.
- **FR-019**: System MUST produce a human-readable brief for each run containing the ranked
  to-dos, their source links, and the run manifest.

**Proposal and review**

- **FR-020**: System MUST produce at most one proposed action per to-do, and none for
  to-dos whose suggested action is "no action".
- **FR-021**: System MUST write each proposed action as a single file combining machine-
  readable metadata with a plain-text, human-editable message body.
- **FR-022**: System MUST assess each proposed action as low, medium, or high risk, and
  MUST assess as high risk anything involving money, a commitment to an external party, or
  a decline.
- **FR-023**: System MUST record, for each proposed action, a rationale naming the sources
  that drove it.
- **FR-024**: System MUST compose drafts according to a stored description of the
  operator's writing voice.
- **FR-025**: System MUST collect a run's proposed actions onto a branch and open a review
  request containing the brief and a table of actions with their risk levels.
- **FR-026**: System MUST mark the review request as high risk when it contains any
  high-risk action.
- **FR-027**: System MUST NOT open a review request for a run that produced no actions.
- **FR-028**: System MUST stop at a configured maximum number of actions per run and state
  when it has done so.

**Execution and safety**

- **FR-029**: System MUST NOT perform any outbound action without both a merged review
  request and an approval recorded on a protected environment by a named reviewer.
- **FR-030**: System MUST verify, before performing an action, that its identifier is
  absent from the ledger, and MUST skip it otherwise.
- **FR-031**: System MUST record every performed action in the ledger with the provider's
  returned identifier as a receipt.
- **FR-032**: System MUST refuse to act on any recipient absent from the configured
  allowlist, and MUST make that refusal visible.
- **FR-033**: System MUST evaluate the allowlist against the file's contents at execution
  time, so that a human edit to a recipient is still checked.
- **FR-034**: System MUST default to a mode in which actions are logged but not performed.
- **FR-035**: System MUST move performed actions to a sent queue and failed actions to a
  failed queue carrying the error, and MUST continue with remaining actions after a
  failure.
- **FR-036**: System MUST raise a tracked issue for each failed action.

**Contracts, observability, and testing**

- **FR-037**: System MUST validate every agent's output against a declared schema, retry
  once with the validation error on failure, and fail the run loudly on a second failure.
- **FR-038**: System MUST NOT coerce, drop, or silently repair fields that fail validation.
- **FR-039**: System MUST log every model call with a prompt hash, the model name and
  pinned version, input and output token counts, and latency.
- **FR-040**: System MUST report per-run token spend in the review request.
- **FR-041**: System MUST be able to run its entire pipeline against recorded fixtures with
  no network access to the providers.
- **FR-042**: System MUST provide a means of capturing and redacting live provider
  responses into those fixtures.
- **FR-043**: System MUST hold no credential in version control, and MUST obtain provider
  and platform access interactively when run by a human and by federated identity when run
  automatically.
- **FR-044**: System MUST source agent definitions from version control and provision them
  to the hosting platform, treating the repository as authoritative.
- **FR-045**: System MUST restrict automatic runs to mail and calendar, because chat
  retrieval under an unattended identity requires provider approval that is not in place;
  chat is retrieved only in attended runs.

### Key Entities

- **Source Reference**: a pointer back to one originating message — its kind, identifier,
  thread, deep link, author, timestamp, and a short excerpt for display. The unit of
  provenance; everything downstream carries a collection of these.
- **Signal**: one extracted observation from one source — an ask, a commitment the operator
  made, an item requiring no action, a stated deadline, a meeting preparation need, or a
  scheduling conflict. Carries its statement, the counterparty if identifiable, an explicit
  due date if stated, an ambiguity flag, and its sources.
- **To-do Item**: one deduplicated unit of work, formed by merging signals that describe
  the same ask. Carries a stable identifier, title and detail, owner, due date, urgency
  score with its written reason, suggested action, confidence, a human-judgement flag, and
  the union of its sources.
- **Proposed Action**: one concrete outbound act the system would perform to advance a
  to-do — a reply, a new message, a meeting, a chat post, or a tracked issue. Carries its
  own identifier, which is also its idempotency key, its risk level, its targets, the
  editable message body, a rationale, and its sources.
- **Run Manifest**: the record of one execution — the window queried, sources used, counts
  at each stage, token spend, and wall time.
- **Ledger**: the durable record of which proposed actions have been performed, keyed by
  identifier and carrying the provider's receipt. The sole authority on whether something
  has already been sent.
- **Allowlist**: the enumerated set of recipients the system may contact. Absence from it is
  a hard failure, not a warning.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An ask arriving through three channels produces exactly one to-do carrying
  three source references, in every run against the seeded mailbox.
- **SC-002**: Across the seeded mailbox, the number of due dates present in output but not
  explicitly stated in any source is zero.
- **SC-003**: A commitment buried in a long thread is present in the brief.
- **SC-004**: An email with urgent phrasing but no request ranks below every item that has
  an explicit deadline or an important sender.
- **SC-005**: Two consecutive runs against an unchanged mailbox produce byte-identical
  to-do identifiers and an empty diff.
- **SC-006**: Executing the same proposed action twice results in exactly one outbound
  action.
- **SC-007**: No outbound action occurs in any code path that has not passed both a merged
  review request and a recorded environment approval.
- **SC-008**: An attempt to act on a recipient outside the allowlist fails without
  contacting the provider.
- **SC-009**: The full test suite passes with no network access to the providers and no
  credentials present.
- **SC-010**: The consolidation evaluation meets its thresholds for duplicate recall,
  false-merge rate, and buried-commitment recall, with an invented-deadline count of zero.
- **SC-011**: The majority of test cases exercise deterministic code paths containing no
  model call.
- **SC-012**: Every to-do in the brief links to at least one source message, and every link
  resolves.
- **SC-013**: A reviewer can read a run's cost and duration without leaving the review
  request.
- **SC-014**: A run over roughly thirty messages completes within the time an operator will
  wait for a morning brief — under two minutes.
- **SC-015**: The system can be brought up from a clean clone by following the readme in
  under five minutes, excluding provider tenant setup.

## Assumptions

Three questions were open in the source material and are resolved here as defaults rather
than left as blockers. Each is configurable.

- **Time window defaults to the last 24 hours**, extended backwards across a weekend or
  holiday so that a Monday run covers Friday evening onward. Calendar look-ahead is today
  plus the next two business days. Both are settings, and the operator can override the
  window per run.
- **Sender importance is an explicit configured list**, not an inferred one. An entry may
  match a full address or a domain, and carries a weight used by the urgency computation.
  A sender absent from the list is neither important nor unimportant; they contribute
  nothing to the score, and the explicit-deadline and multi-source components still apply.
  Inferring importance from interaction history is out of scope: it is unreviewable, and
  the operator must be able to look at a file and know why something ranked where it did.
- **A to-do that is neither actioned nor dismissed simply recurs.** Because identifiers are
  content-derived, the next run re-derives the same item; the brief records how many
  consecutive runs it has appeared in, and an item persisting beyond a configured number of
  runs is surfaced as stale. Dismissal is an explicit act: the operator closes the review
  request or deletes the pending file, and the identifier is recorded so it is not proposed
  again. There is no automatic expiry — silently dropping an unactioned ask is precisely
  the failure the system exists to prevent.

Further assumptions:

- The operator is a single individual reviewing their own correspondence. Multi-user,
  shared-mailbox, and delegate-access scenarios are out of scope.
- The demo mailbox is dedicated and seeded with fictional traffic; no real third-party
  correspondence passes through the system.
- Chat retrieval is available only in attended runs, per FR-045. Unattended runs cover mail
  and calendar, and their briefs say so.
- Deep links into the provider's clients are stable enough to serve as citations in a
  review request.
- English-language correspondence. Multilingual extraction is untested and out of scope.
- The operator has, or can obtain, a provider tenant they administer. Tenant provisioning
  is a prerequisite, not part of this feature.
