# Run of show — 25 minutes, no Q&A

| Time | Beat | On screen |
|---|---|---|
| 0:00–2:30 | **The problem.** Monday morning, three tools, the same ask three times. | One image. No bullets. |
| 2:30–5:00 | **`ckpt-1-naive` runs live.** One prompt, all the context. It is confidently wrong: three to-dos for one ask, the buried commitment missed, a deadline invented from "soon-ish". Name each error as it appears. | Terminal |
| 5:00–7:00 | **Architecture.** Why specialists. Why the orchestrator routes ingest and code routes everything after. | One slide |
| 7:00–14:00 | **Build the consolidator live.** Two prompts from `docs/live-prompts.md`. Include the moment Copilot reaches for a model to do the clustering, and fix it by hand. Land the line. Re-run: the triple collapses. | VS Code + terminal |
| 14:00–19:00 | **Build the approval step.** Drafts to `outbox/pending/`, branch, pull request. Open it. Edit a draft in the web editor. Show the diff. | Browser |
| 19:00–22:00 | **The payoff.** `execute.yml`, the protected environment, the allowlist, the ledger. Merge. Approve. The mail arrives. | Browser + phone |
| 22:00–25:00 | **What broke and what it cost.** Two platform refusals. The scored evaluation. $0.09 a run. QR code. | Two slides |

## The one beat that cannot be cut

**19:00–22:00.** If you are running long, cut depth in the consolidator build — do one
Copilot prompt instead of two and narrate the second. Do not cut the payoff; it is the
only part that makes "prompt to pipeline" literal.

## Pre-flight, 30 minutes before

```bash
az account show                        # correct subscription
uv run cos login                       # token fresh — the most likely failure
cat config/allowed_recipients.yaml     # know what it says before you show it
grep dry_run config/settings.yaml      # know which mode you are in
uv run pytest -q                       # green
uv run cos brief --raw                 # retrieval works right now
git branch -a | grep ckpt              # all five checkpoints present
```

## What to say about the things that went wrong

Do not hide these. They are the most credible three minutes of the talk.

- **Teams chat is not in the demo.** Application-permission access needs Microsoft's
  protected API approval, measured in weeks. Consumer accounts do not expose chat through
  Graph at all. Two independent refusals, and the brief says so at runtime rather than
  quietly under-reporting.
- **The tenant took four attempts.** The developer program no longer qualifies on a
  Visual Studio benefit; an Azure-created directory has no commerce backend and refuses
  consumer identities; the work tenant blocks app registration outright. What finally
  worked was a consumer-only app registration and a personal Microsoft account.
- **Three bugs only appeared once real output existed.** Every deadline was silently
  dropped because models return dates without an offset. Fifteen inert notices bundled
  into a to-do that outranked real work. Sender importance was configured by address and
  matched against a display name, so it never fired — and ranked a CAD 47,500 overdue
  invoice at 7 out of 100.

That last one is the best slide in the deck. It is what "where it gets things wrong"
actually looks like.
