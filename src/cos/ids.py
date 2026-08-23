"""Content-derived identifiers.

Both identifiers are pure functions of content. That is what produces a clean diff when
the pipeline re-runs against an unchanged inbox: the same ask yields the same id, the
same filename, and no change (FR-016, SC-005).

The identifiers *look* like ULIDs — 26 characters, Crockford base-32, lexicographically
sortable — but their timestamp component is fixed and their random component is a hash.
A real timestamp would change the id on every run, which is precisely the failure being
designed out.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Crockford base-32, the ULID alphabet. Excludes I, L, O, and U.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Fixed timestamp component. 2026-01-01T00:00:00Z in milliseconds, so ids from this
# system sort together and are visibly not wall-clock ULIDs to anyone who checks.
_FIXED_TIMESTAMP_MS = 1767225600000

_WHITESPACE = re.compile(r"\s+")
# The en and em dashes are intentional: real mail subjects contain them.
_TRAILING_PUNCT = re.compile(r"[\s.,;:!?\-–—]+$")


def normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip trailing punctuation.

    Deliberately does not stem or lemmatise. Two statements that differ by a word are
    two different asks, and deciding otherwise is the merge model's job, not the hash's.
    """
    return _TRAILING_PUNCT.sub("", _WHITESPACE.sub(" ", text.strip().lower()))


def _encode_crockford(value: int, length: int) -> str:
    out: list[str] = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def ulid_from_hash(digest: bytes) -> str:
    """Build a 26-character ULID-shaped id from a digest.

    10 characters of fixed timestamp, 16 characters carrying 80 bits of the digest.
    """
    timestamp = _encode_crockford(_FIXED_TIMESTAMP_MS, 10)
    randomness = _encode_crockford(int.from_bytes(digest[:10], "big"), 16)
    return timestamp + randomness


def _digest(*parts: str) -> bytes:
    # NUL-separated so that ("ab", "c") and ("a", "bc") cannot collide.
    return hashlib.blake2b(b"\x00".join(p.encode("utf-8") for p in parts), digest_size=16).digest()


def _source_key(kind: str, source_id: str) -> str:
    return f"{kind}:{source_id}"


def todo_id(statement: str, sources: list[tuple[str, str]]) -> str:
    """Derive a to-do id from its merged statement and its sources.

    `sources` is a list of `(kind, id)` pairs. They are sorted and deduplicated, so the
    order signals were merged in cannot change the resulting identifier.
    """
    keys = sorted({_source_key(kind, sid) for kind, sid in sources})
    return ulid_from_hash(_digest(normalise(statement), "\x00".join(keys)))


def _canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace.

    The target is part of the idempotency key, so `{"to": ["a"], "cc": []}` must hash
    identically however the dict happened to be ordered in memory.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def action_id(todo: str, kind: str, target: Any) -> str:
    """Derive an action id — the idempotency key — from its to-do, kind, and target.

    Including the target is what makes it safe: two different replies to the same to-do
    are two different actions, and re-deriving one after a human edits the *body* yields
    the same id, so an edited draft is still the same single send.
    """
    payload = target.model_dump(mode="json") if hasattr(target, "model_dump") else target
    return ulid_from_hash(_digest(todo, kind, _canonical_json(payload)))
