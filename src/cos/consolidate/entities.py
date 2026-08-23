"""Entity-key extraction.

Regexes over message text, pulling out the identifiers that two messages about the same
thing tend to share: a ticket, a pull request, a document, an invoice. Two signals that
cite the same JIRA ticket are talking about the same ticket, and no model is needed to
notice that.

This module contains no model call and must never acquire one (Constitution III).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# Ticket and issue references. Deliberately requires a hyphen and a project prefix of at
# least two letters, because bare numbers match everything and merge unrelated asks.
_TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")

# GitHub and Azure DevOps pull requests, and bare #123 references.
_PR_URL = re.compile(
    r"\bhttps?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:pull|issues)/(\d+)", re.I
)
_ADO_PR = re.compile(r"\bhttps?://dev\.azure\.com/([\w.-]+)/([\w.-]+)/.*?/pullrequest/(\d+)", re.I)
_HASH_REF = re.compile(r"(?<![\w#])#(\d{1,6})\b")

# Documents: SharePoint, OneDrive, Google Docs.
_DOC_URL = re.compile(
    r"\bhttps?://[\w.-]*(?:sharepoint\.com|onedrive\.live\.com|docs\.google\.com)/\S+", re.I
)

# Money references that tend to identify one conversation.
_INVOICE = re.compile(
    r"\b(?:invoice|inv|po|purchase\s+order|order)[\s#:.-]*([A-Z0-9-]{4,20})\b", re.I
)

# Tracking parameters, stripped so the same document shared twice produces one key.
_TRACKING_PARAMS = ("utm_", "e=", "csf=", "web=", "share", "at=", "referrer")


def canonical_url(url: str) -> str:
    """Strip tracking noise so two shares of one document produce one key.

    A SharePoint link forwarded through Outlook and the same link pasted into Teams differ
    only in their query string. Without this they cluster as two different documents.
    """
    parts = urlsplit(url.rstrip(").,;>\"'"))
    query = "&".join(
        p
        for p in parts.query.split("&")
        if p and not any(p.lower().startswith(t) for t in _TRACKING_PARAMS)
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, "")).rstrip(
        "/"
    )


def extract_entity_keys(text: str) -> set[str]:
    """Return the normalised entity keys present in a piece of text.

    Keys are namespaced (`ticket:`, `pr:`, `doc:`, `invoice:`) so a ticket numbered 42 and
    an invoice numbered 42 never collide into one cluster.
    """
    if not text:
        return set()

    keys: set[str] = set()

    for match in _TICKET.finditer(text):
        keys.add(f"ticket:{match.group(1).upper()}")

    for owner, repo, number in _PR_URL.findall(text):
        keys.add(f"pr:github/{owner.lower()}/{repo.lower()}#{number}")

    for org, project, number in _ADO_PR.findall(text):
        keys.add(f"pr:ado/{org.lower()}/{project.lower()}#{number}")

    for number in _HASH_REF.findall(text):
        keys.add(f"pr:ref#{number}")

    for url in _DOC_URL.findall(text):
        keys.add(f"doc:{canonical_url(url)}")

    for number in _INVOICE.findall(text):
        value = number.upper().strip("-")
        # Guard against matching the word after "order" in ordinary prose — an entity key
        # needs at least one digit to be an identifier rather than a noun.
        if any(c.isdigit() for c in value):
            keys.add(f"invoice:{value}")

    return keys
