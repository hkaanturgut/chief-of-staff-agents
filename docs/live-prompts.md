# Live prompts

Copilot Agent Mode prompts for the stage. Rehearse each until it lands in **under 90
seconds**. If one is not working after 90 seconds, check out the next checkpoint branch
and narrate the diff — that is the plan, not the fallback.

The code these produce already exists on `main`. You are re-deriving it in front of
people, which means you know what good looks like and can correct Copilot the moment it
drifts.

---

## Beat 1 — the deterministic pre-pass (7:00–10:00)

> Implement `src/cos/consolidate/prepass.py`. Group `Signal` objects into candidate
> clusters using union-find over three independent key families: conversation and thread
> identity from the provider, normalised subject with reply and forward prefixes
> stripped, and shared entity keys extracted by regex. Pure functions, no LLM anywhere in
> the module. Return clusters ordered by their earliest member so the output is stable
> across runs.

**What Copilot usually gets wrong, and what to say when it does:**

- It reaches for an embedding model or an LLM call to decide similarity. This is the
  moment. *"No — this is exactly the thing I don't want. Two messages in the same thread
  are the same thread. That's a hash lookup, not a judgement call."*
- It clusters on raw subject without stripping `Re:`/`Fwd:`. Ask it what happens to
  `Re: RE: Fwd: Q3 renewal`.
- It clusters on *any* shared subject, including "Update" and "Quick question". Ask it
  what happens when two people both send "Update".

**The line:** *do not pay a language model to do what a hash can do.*

---

## Beat 2 — the merge stage (10:00–14:00)

> Implement `src/cos/consolidate/merge.py`. For each candidate cluster, call the
> consolidator agent to decide merge or split and write the merged statement. Return
> `TodoItem[]` per `src/cos/models.py`. The model's response schema must NOT contain
> urgency, due, sources, or id — urgency is computed in `rank.py`, due is carried forward
> from whichever signal explicitly stated it, sources are the union assembled in code,
> and the id is derived from content afterwards.

**Where to slow down:** when Copilot writes a schema with `urgency` in it — and it will —
that is the second teaching moment. *"If the model can set the score, the score isn't
reviewable. It supplies the sentence; the code supplies the number."*

Then run it. The triple collapses from three to-dos to one carrying four sources.

---

## Beat 3 — the approval step (14:00–19:00)

> Implement `src/cos/outbox/writer.py`. Write each `ProposedAction` as a markdown file
> with YAML frontmatter to `outbox/pending/<action_id>.md`, then create a branch, commit,
> and open a pull request whose body is BRIEF.md plus a risk-labelled action table. The
> body below the frontmatter is the message that gets sent, so a human editing it in the
> GitHub web editor is authoritative.

Then, on screen:

1. Open the pull request.
2. Edit a draft body in the web editor. Show the diff — agent proposal versus human edit.
3. Show `config/allowed_recipients.yaml`. Read it out loud. **This is the slide people
   remember.**

---

## Recovery lines

Have these ready, said calmly:

- Token expired: *"This is the failure I predicted in the risks section — twenty seconds."*
  Then `uv run cos login`.
- Graph throttles: *"Graph is rate-limiting me, which is exactly why there's backoff in
  the client."* Then switch to `cos brief --corpus`.
- Copilot produces something unusable: *"That's the wrong shape, and rather than fight it
  live, here's the version I'd ship."* Then `git checkout ckpt-2-consolidator`.

Every one of these turns a failure into content. Rehearse them like lines, because they
are lines.
