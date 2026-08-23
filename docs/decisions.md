# Decisions and blockers

Running log of calls made during the build, and the one thing that needs a human.

---

## B-001 — BLOCKER: the Azure tenant has no Microsoft 365

**Status: RESOLVED 2026-08-23.** A personal Microsoft account, no tenant required. Details
at the end of this entry.

**Update 2026-08-23.** Four routes tried, three closed. Recorded here so nobody spends
another morning on them.

| Route | Outcome |
|---|---|
| M365 Developer Program E5 sandbox | *"You don't currently qualify."* Microsoft tightened eligibility; the Visual Studio Enterprise benefit no longer grants it. |
| M365 trial inside `kaanturgutbusinessgmail.onmicrosoft.com` | Admin centre refuses the signed-in identity outright — error 100014, *"Login is not supported for consumer users without business presence."* The account is a personal Microsoft account guest-invited into the directory, so no role assignment can fix it. Creating a native cloud member with Global Administrator got past that, and then checkout itself failed: this is a Default Directory Azure created to hold a subscription, and it has no commerce backend. |
| A dedicated mailbox in `deop.ca` | Kaan holds **User Administrator** there — enough to create a licensed mailbox, and there are 106 free `Microsoft_365_E3_(no_Teams)` seats. Not enough for anything that would authenticate to it: `allowedToCreateApps` is `False` tenant-wide, and the consent policy is `microsoft-user-default-low`, which does not cover `Mail.Read`, `Mail.Send`, or `Calendars.ReadWrite`. App registration and admin consent both need someone else. Separately, it is an employer tenant, and a projector pointed at it shows the company domain and colleagues' names in every recipient autocomplete — Hard rule 2. |
| A personal `outlook.com` account under a multi-tenant app | This directory accepts `signInAudience: AzureADMultipleOrgs` but **rejects `AzureADandPersonalMicrosoftAccounts`**. A Default Directory has no Microsoft-account federation, so it cannot host an app that accepts consumer sign-ins. |

**Route still open, and the only reliable one:** a standalone Microsoft 365 Business Basic
trial signed up from the marketing page with a *new* account and a *new* domain, which
creates a normal commerce-enabled tenant with Global Administrator from the first minute.
Five minutes, a card for identity verification, not charged during the trial.

App registration `chief-of-staff-graph` (`757b8da4-1eb3-4aef-9ac7-b26b0afd959a`) is left as
`AzureADMultipleOrgs`, so it will work against whatever work/school tenant appears without
needing a second registration — only consent.


The spec names tenant `ffe3d4fb-2c1a-4bee-be2f-4b6e78f182c9` (Visual Studio Enterprise
Subscription, `kaanturgutbusinessgmail.onmicrosoft.com`). Two facts about it:

- `GET /v1.0/subscribedSkus` returns an empty list. No M365 licences of any kind, so
  no Exchange Online, no mailboxes, no Teams.
- The operator account is a guest: `kaanturgutbusiness_gmail.com#EXT#@kaanturgutbusinessgmail.onmicrosoft.com`,
  with `mail: null`. It is a personal Gmail identity federated into a directory that
  Azure created to hold the subscription. It was never an M365 identity.

So there is nothing in that tenant for Graph to read. `Mail.Read` against it returns
nothing because there is no mailbox to return.

The only live M365 identity available is `kaan.turgut@deop.ca` — Kaan's employer's
tenant. Pointing the demo at it violates Hard rule 2 (no client data on screen), and
seeding it with fictional traffic needs admin rights there.

**Recommended fix: a Microsoft 365 E5 Developer subscription.** Free, 25 seats, its own
isolated tenant, renews while it is being used. It is the standard instrument for this
exact situation and it is what §6.1 describes when it says "a dedicated M365 demo
tenant". Kaan's Visual Studio Enterprise subscription is a qualifying benefit, so he
should be eligible. Sign-up is a browser flow and cannot be automated:

1. <https://developer.microsoft.com/microsoft-365/dev-program> → Join, signing in with
   the Visual Studio Enterprise account.
2. Choose the **instant sandbox**. It arrives pre-populated with users and sample mail,
   which shortens §8 seeding considerably.
3. Record the tenant id, the primary demo mailbox UPN, and two or three alt-account
   UPNs to send the planted traps from.
4. Put them in `.env` as `GRAPH_TENANT_ID`, `GRAPH_MAILBOX`, and in
   `config/allowed_recipients.yaml`.

**Fallback if the dev tenant does not land:** a dedicated, licensed, otherwise-empty
mailbox in a tenant Kaan controls, with `config/allowed_recipients.yaml` restricted to
that one address. Same architecture, more care needed about what is on screen.

**What this does not block.** Everything else. The Graph layer is written against the
normalised models in `src/cos/models.py`, with tenant and mailbox read from config, so
pointing it at a tenant is a settings change rather than a code change. Fixtures drive
the tests, so CI is green without any tenant at all. The build continues.

---

## D-001 — Model tiers: gpt-5.4-mini and gpt-5.5, not gpt-4.1

The spec said "cheap tier (e.g. `gpt-4.1-mini`)" and "strong tier", with `gpt-4.1` in
the sample frontmatter. The `e.g.` reads as a placeholder rather than a requirement, and
gpt-4.1 is two generations old as of August 2026 — an audience of Azure engineers will
notice. `canadaeast` has 1000K TPM of headroom on both replacements.

- Ingest agents (`mail-triage`, `chat-triage`, `calendar-context`): `gpt-5.4-mini`,
  version `2026-03-17`.
- Consolidator and drafter: `gpt-5.5`, version `2026-04-24`.

Both are `GlobalStandard` at 100K TPM and pinned with `versionUpgradeOption:
NoAutoUpgrade`, so a model refresh cannot change behaviour between the rehearsal and
the talk. That property is worth saying out loud on stage.

## D-002 — Region: canadaeast

Quota exists in both `canadaeast` and `eastus2`. Canada East wins on the narrative:
Toronto audience, data stays in Canada, and the room will have opinions about
residency. No technical cost.

## D-003 — Two tenants, two credential chains

Following on from B-001: the Foundry account lives in the Visual Studio subscription's
directory and the mailbox will live in a different M365 tenant. One `DefaultAzureCredential`
cannot serve both — the token audiences and the home tenants differ. So the config carries
`AZURE_TENANT_ID` and `GRAPH_TENANT_ID` separately, and `src/cos/graph/auth.py` builds its
own credential with an explicit tenant rather than inheriting the ambient Azure login.

This is not a workaround; it is what the separation would look like in any real
deployment, where the AI platform subscription and the productivity tenant are rarely
the same directory.

## D-004 — Branch protection requires a PR but zero approvals

§0.3 asks for "a required PR review" on `main`. Taken literally that deadlocks the demo:
GitHub does not let anyone approve their own pull request, Kaan is the only collaborator,
and on stage he is the one opening the PR. A required approval count of 1 would leave the
merge button disabled in front of the audience.

So the `protect-main` ruleset requires a pull request with
`required_approving_review_count: 0`, and blocks deletion and force-push. The human gate
is not weakened, it has moved: the real approval is the `send` environment, which does
require a named reviewer to click, and which does permit self-review. Two gates, as §5
demands — the PR is the review surface, the environment is the authorisation.

Repository admin keeps bypass, so a wedged rule is never what ends the talk.

## D-005 — CI status checks are advisory, not required-to-merge

Same reasoning as D-004. A required status check that never reports blocks the merge, and
the merge is the one beat in the run of show that cannot be cut. `ci.yml` runs on every PR
and its result is visible in the PR, which is all the demo needs it to be.

## D-006 — Infrastructure is Azure Verified Modules

`infra/main.bicep` composes `avm/ptn/ai-ml/ai-foundry`, `avm/res/resources/resource-group`,
and `avm/res/consumption/budget` rather than declaring resources by hand. The pattern
module owns the account/project/deployment relationship, including the ordering constraint
that the control plane rejects concurrent deployment writes to one account.

`includeAssociatedResources` is `false`: no Key Vault, AI Search, Storage, or Cosmos. The
pipeline keeps no state in Azure — the repository is the state, which is the whole point of
the talk — and the subscription is credit-capped.

`disableLocalAuth` is `true`, so there are no API keys to leak from a public repo. Entra
RBAC is the only way in, for the operator and for CI alike.

## D-007 — CI authenticates with three federated credentials, no secrets

App registration `chief-of-staff-demo` carries GitHub OIDC federated credentials for
`ref:refs/heads/main` (execute), `environment:send` (the gated job), and `pull_request`
(CI). Its service principal holds Foundry User on the account, scoped no wider.

The repository contains no client secret and no API key. Actions **variables** carry the
subscription, tenant, and client ids; they are identifiers rather than credentials, and
`azure/login` needs them by name.

## D-008 — Time window: last 24h, extended over non-working days

`/speckit-clarify` would have asked this. Answering rather than blocking: the default
window is the last 24 hours, extended backwards across weekends and holidays so a Monday
run reaches back to Friday evening. Calendar look-ahead is today plus two business days.
Both are settings in `config/settings.yaml` and both are overridable per run.

A fixed 24 hours would silently drop everything that arrived over a weekend, which is
exactly when a Monday-morning brief is most needed.

## D-009 — Sender importance is a configured list, never inferred

`config/important_senders.yaml` holds explicit entries — full address or domain — each with
a weight feeding the urgency computation. A sender not on the list contributes zero; the
explicit-deadline and distinct-source components still apply, so an unknown sender with a
real deadline still ranks.

Inferring importance from interaction history was rejected. It is unreviewable, it drifts
between runs, and the operator must be able to open one file and understand why an item
ranked where it did. "Urgency is computed, not vibed" only holds if every input to the
computation is inspectable.

## D-010 — An unactioned to-do recurs; it never expires

The third open question. Because `TodoItem.id` is content-derived, an item that is neither
actioned nor dismissed is simply re-derived identically on the next run. The brief carries
a count of consecutive runs an item has appeared in, and anything past a configured
threshold is surfaced as stale.

Dismissal is explicit: closing the review request or deleting the pending file records the
identifier so it is not proposed again. There is deliberately no automatic expiry — an ask
that quietly ages out of the list is the precise failure this system exists to prevent.
