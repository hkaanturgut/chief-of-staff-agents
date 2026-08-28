#!/usr/bin/env python
"""Seed the demo mailbox with the planted traps.

Talk scaffolding, deliberately outside `src/`. It is real code that sends real mail, but
it is not part of the product.

## What this does, and what it deliberately does not

It sends the corpus messages **from the operator's own account to itself**, through
`sendMail`. Real Graph calls, real delivery, real timestamps.

It does NOT fabricate senders. Making a message appear to come from `priya@northwind` —
by writing a `from` address and clearing the unsent flag through extended properties — is
message forgery in mechanism, whatever the intent, and this repository is not going to
demonstrate the technique on a conference stage. The personas live in the subject and
body instead, where the extraction agents read them from anyway: `counterparty` is
inferred from content, not from the From header.

The cost is that seeded mail is authored by the operator, so `is_from_operator` is true
for all of it and sender weighting does not apply. Two consequences worth knowing before
you rely on this:

  * Use `--dry-run` first. Always.
  * For a rehearsal where sender weighting matters, `cos brief --corpus` is the better
    instrument — it exercises the identical pipeline with the personas intact.

The honest way to get real multi-sender traffic is more than one real mailbox. Two free
outlook.com accounts sending to a third is fifteen minutes of setup and no forgery at all.
"""

from __future__ import annotations

import argparse
import sys
import time

from cos.corpus import load
from cos.graph.auth import GraphAuthError, from_settings
from cos.graph.client import GraphClient, GraphError
from cos.settings import load_environment


def build(subject: str, persona: str, body: str, to: str) -> dict:
    # The persona is stated in the body, where the agents read it from, rather than
    # forged into the envelope.
    content = f"[demo message — from {persona}]\n\n{body}"
    return {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": content},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print, send nothing.")
    parser.add_argument("--limit", type=int, default=0, help="Send at most N messages.")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between sends.")
    args = parser.parse_args()

    env = load_environment()
    env.require("graph_mailbox")
    mailbox = env.graph_mailbox
    assert mailbox

    bundle, _, _ = load()
    messages = bundle.mail[: args.limit] if args.limit else bundle.mail

    print(f"{len(messages)} message(s) -> {mailbox}")
    if args.dry_run:
        for m in messages:
            print(f"  would send: [{m.from_name}] {m.subject}")
        print("\nDry run. Nothing sent.")
        return 0

    print("\nThis sends real mail to your own mailbox. Ctrl-C now if that is not what")
    print("you want. Starting in 5 seconds.")
    time.sleep(5)

    try:
        auth = from_settings()
        auth.token()
    except GraphAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sent = 0
    with GraphClient(auth=auth) as client:
        for message in messages:
            payload = build(
                message.subject,
                message.from_name or message.from_address,
                message.body_text,
                mailbox,
            )
            try:
                client.post("/me/sendMail", payload)
                sent += 1
                print(f"  sent: {message.subject[:60]}")
            except GraphError as exc:
                print(f"  FAILED: {message.subject[:50]} — {exc}", file=sys.stderr)
            time.sleep(args.delay)

    print(f"\n{sent}/{len(messages)} sent. Give Graph a minute, then `cos brief --raw`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
