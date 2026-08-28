"""Stage A: deterministic clustering.

Groups signals into *candidate* clusters using three independent key families:

1. conversation and thread identity, straight from the provider
2. normalised subject, with reply and forward prefixes stripped
3. shared entity keys — tickets, pull requests, documents, invoices

Signals sharing any key join the same cluster, via union-find.

**No model call. Ever.** This module is the reason most of the system is testable, and
`tests/test_determinism_imports.py` fails if anything here reaches the agent runner.

The union is deliberately generous. Stage A optimises for *recall*, because a candidate
pair it misses can never be recovered downstream, whereas an over-eager grouping is
exactly what Stage B is there to split.

    Do not pay a language model to do what a hash can do.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from cos.consolidate.entities import extract_entity_keys
from cos.models import Signal

# Reply and forward prefixes across the languages a Toronto mailbox actually sees, plus
# the ones Outlook generates. Applied repeatedly, because "Re: Fwd: RE:" is normal.
_PREFIX = re.compile(
    r"^\s*(?:re|aw|fw|fwd|tr|rv|sv|vs|antw|res|回复|答复)\s*(?:\[\d+\])?\s*:\s*",
    re.IGNORECASE,
)
# Outlook's bracketed tags: [EXTERNAL], [SPAM], mailing-list prefixes.
_BRACKET_TAG = re.compile(r"^\s*\[[^\]]{1,32}\]\s*")
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")

# A normalised subject shorter than this is too generic to cluster on. "Hi", "Question",
# and "Update" are not evidence that two messages are about the same thing.
MIN_SUBJECT_KEY_LEN = 8

_GENERIC_SUBJECTS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "question",
        "update",
        "quick question",
        "follow up",
        "following up",
        "checking in",
        "touching base",
        "thanks",
        "thank you",
        "fyi",
        "meeting",
        "catch up",
        "sync",
        "reminder",
        "no subject",
    }
)


def normalise_subject(subject: str) -> str:
    """Strip reply/forward prefixes, bracketed tags, punctuation, and case.

    `Re: RE: [EXTERNAL] Fwd: Q3 Vendor Renewal!` becomes `q3 vendor renewal`.
    """
    value = subject or ""
    changed = True
    while changed:
        changed = False
        stripped = _PREFIX.sub("", value)
        if stripped != value:
            value, changed = stripped, True
        stripped = _BRACKET_TAG.sub("", value)
        if stripped != value:
            value, changed = stripped, True
    return _WS.sub(" ", _PUNCT.sub(" ", value)).strip().lower()


def subject_key(subject: str) -> str | None:
    """A clustering key for a subject, or None when the subject is too generic to trust."""
    value = normalise_subject(subject)
    if len(value) < MIN_SUBJECT_KEY_LEN or value in _GENERIC_SUBJECTS:
        return None
    return f"subject:{value}"


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


@dataclass(frozen=True)
class Cluster:
    """A candidate group of signals, with the evidence that grouped them.

    `keys` is kept because it is what makes a wrong merge debuggable: when Stage B splits
    a cluster, the key that wrongly joined it is right there.
    """

    indices: tuple[int, ...]
    keys: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_singleton(self) -> bool:
        return len(self.indices) == 1


def signal_keys(signal: Signal, *, subject_lookup: dict[str, str] | None = None) -> set[str]:
    """Every clustering key a signal carries."""
    keys: set[str] = set()

    for source in signal.sources:
        if source.thread_id:
            keys.add(f"thread:{source.kind}:{source.thread_id}")
        # A signal citing a message is joined to any other signal citing the same
        # message. Two extractions from one email are one ask.
        keys.add(f"message:{source.kind}:{source.id}")

        if subject_lookup:
            subject = subject_lookup.get(source.id)
            if subject and (key := subject_key(subject)):
                keys.add(key)

    keys |= extract_entity_keys(signal.statement)
    for source in signal.sources:
        keys |= extract_entity_keys(source.excerpt)

    return keys


def build_clusters(
    signals: Sequence[Signal],
    *,
    subject_lookup: dict[str, str] | None = None,
) -> list[Cluster]:
    """Group signals into candidate clusters. Pure, deterministic, no model.

    `subject_lookup` maps a source id to its raw subject, so mail subjects can act as a
    key without `Signal` having to carry them. Chat and calendar simply do not contribute
    subject keys.

    Clusters come back ordered by their earliest member, so the output is stable across
    runs — which is what keeps the pull request diff clean.
    """
    if not signals:
        return []

    keys_per_signal = [signal_keys(s, subject_lookup=subject_lookup) for s in signals]

    owner: dict[str, int] = {}
    dsu = _DisjointSet(len(signals))
    for index, keys in enumerate(keys_per_signal):
        for key in keys:
            if key in owner:
                dsu.union(owner[key], index)
            else:
                owner[key] = index

    grouped: dict[int, list[int]] = {}
    for index in range(len(signals)):
        grouped.setdefault(dsu.find(index), []).append(index)

    clusters = [
        Cluster(
            indices=tuple(sorted(members)),
            keys=frozenset().union(*(keys_per_signal[i] for i in members)),
        )
        for members in grouped.values()
    ]
    clusters.sort(key=lambda c: c.indices[0])
    return clusters


def cluster_signals(
    signals: Sequence[Signal],
    *,
    subject_lookup: dict[str, str] | None = None,
) -> list[list[Signal]]:
    """Convenience wrapper returning the signals themselves rather than indices."""
    return [
        [signals[i] for i in cluster.indices]
        for cluster in build_clusters(signals, subject_lookup=subject_lookup)
    ]


def describe(clusters: Iterable[Cluster]) -> str:
    """A one-line-per-cluster summary. Useful on stage, and in a failing test."""
    return "\n".join(
        f"cluster {n}: signals {list(c.indices)} via {sorted(c.keys)[:4]}"
        for n, c in enumerate(clusters)
    )
