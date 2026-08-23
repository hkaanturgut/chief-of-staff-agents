"""The deterministic pre-pass.

This is the largest test file in the repository, and that is the point. Every behaviour
pushed into deterministic code is a behaviour that can be tested exactly rather than
scored tolerantly. No model is in the loop here, so these assertions are equalities.

The planted traps from the specification appear by name, so a failure says which one
regressed rather than which line number moved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cos.consolidate.entities import canonical_url, extract_entity_keys
from cos.consolidate.prepass import (
    MIN_SUBJECT_KEY_LEN,
    build_clusters,
    cluster_signals,
    normalise_subject,
    signal_keys,
    subject_key,
)
from cos.models import Signal, SourceRef

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def ref(
    kind: str = "mail",
    id_: str = "m1",
    *,
    thread: str | None = None,
    author: str = "Priya Raman",
    excerpt: str = "",
    offset_hours: int = 0,
) -> SourceRef:
    return SourceRef(
        kind=kind,
        id=id_,
        thread_id=thread,
        author=author,
        timestamp=NOW + timedelta(hours=offset_hours),
        excerpt=excerpt,
    )


def sig(statement: str, *sources: SourceRef, type_: str = "ask", **kw: object) -> Signal:
    return Signal(type=type_, statement=statement, sources=list(sources), **kw)  # type: ignore[arg-type]


# ======================================================================================
# subject normalisation
# ======================================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Q3 vendor renewal", "q3 vendor renewal"),
        ("Re: Q3 vendor renewal", "q3 vendor renewal"),
        ("RE: Q3 Vendor Renewal", "q3 vendor renewal"),
        ("Fwd: Q3 vendor renewal", "q3 vendor renewal"),
        ("FW: Q3 vendor renewal", "q3 vendor renewal"),
        ("Re: RE: Fwd: Q3 vendor renewal", "q3 vendor renewal"),
        ("AW: Q3 vendor renewal", "q3 vendor renewal"),  # German Outlook
        ("TR: Q3 vendor renewal", "q3 vendor renewal"),  # French Outlook
        ("SV: Q3 vendor renewal", "q3 vendor renewal"),  # Nordic Outlook
        ("RE[2]: Q3 vendor renewal", "q3 vendor renewal"),  # numbered reply
        ("[EXTERNAL] Q3 vendor renewal", "q3 vendor renewal"),
        ("Re: [EXTERNAL] Fwd: Q3 Vendor Renewal!", "q3 vendor renewal"),
        ("Q3   vendor    renewal", "q3 vendor renewal"),
        ("Q3 vendor renewal???", "q3 vendor renewal"),
        ("", ""),
    ],
)
def test_normalise_subject(raw: str, expected: str) -> None:
    assert normalise_subject(raw) == expected


def test_normalise_subject_is_idempotent() -> None:
    once = normalise_subject("Re: Fwd: RE: Q3 vendor renewal")
    assert normalise_subject(once) == once


@pytest.mark.parametrize(
    "generic",
    ["Hi", "Hello", "Quick question", "Update", "Following up", "Checking in", "FYI", "Re:"],
)
def test_generic_subjects_are_not_clustering_keys(generic: str) -> None:
    """'Update' twice is not evidence that two messages are about the same thing."""
    assert subject_key(generic) is None


def test_short_subjects_are_not_clustering_keys() -> None:
    assert subject_key("Budget") is None
    assert len(normalise_subject("Budget")) < MIN_SUBJECT_KEY_LEN


def test_substantive_subject_is_a_key() -> None:
    assert subject_key("Q3 vendor renewal") == "subject:q3 vendor renewal"


# ======================================================================================
# entity keys
# ======================================================================================


def test_ticket_reference() -> None:
    assert extract_entity_keys("blocked on PROJ-1421 still") == {"ticket:PROJ-1421"}


def test_bare_numbers_are_not_tickets() -> None:
    """A bare number matches everything and would merge unrelated asks."""
    assert extract_entity_keys("we need 1421 units by Friday") == set()


def test_github_pull_request_url() -> None:
    keys = extract_entity_keys("see https://github.com/acme/widgets/pull/81 please")
    assert "pr:github/acme/widgets#81" in keys


def test_document_url_ignores_tracking_parameters() -> None:
    """The same document shared through Outlook and Teams must produce one key."""
    outlook = "https://contoso.sharepoint.com/:w:/r/sites/x/Spec.docx?web=1&e=AbC123"
    teams = "https://contoso.sharepoint.com/:w:/r/sites/x/Spec.docx?utm_source=teams"
    assert extract_entity_keys(outlook) == extract_entity_keys(teams)


def test_canonical_url_strips_trailing_punctuation() -> None:
    assert canonical_url("https://example.com/doc).") == "https://example.com/doc"


def test_invoice_reference() -> None:
    assert "invoice:INV-88421" in extract_entity_keys("chasing invoice INV-88421")


def test_prose_containing_order_is_not_an_invoice() -> None:
    assert extract_entity_keys("in order to proceed we need approval") == set()


def test_namespaces_prevent_collisions() -> None:
    """Ticket 42 and pull request 42 must not cluster together."""
    ticket = extract_entity_keys("ABC-42 is blocked")
    pr = extract_entity_keys("see #42")
    assert ticket.isdisjoint(pr)


# ======================================================================================
# clustering
# ======================================================================================


def test_empty_input() -> None:
    assert build_clusters([]) == []


def test_unrelated_signals_stay_separate() -> None:
    signals = [
        sig("confirm the vendor renewal number", ref(id_="m1")),
        sig("review the onboarding deck", ref(id_="m2")),
        sig("approve the March invoice", ref(id_="m3")),
    ]
    assert len(build_clusters(signals)) == 3


def test_same_message_cited_twice_is_one_cluster() -> None:
    """Two extractions from one email are one ask."""
    signals = [
        sig("confirm the renewal number", ref(id_="m1")),
        sig("send the renewal number upstream", ref(id_="m1")),
    ]
    assert len(build_clusters(signals)) == 1


def test_thread_identity_clusters() -> None:
    signals = [
        sig("confirm the number", ref(id_="m1", thread="t-9")),
        sig("still need the number", ref(id_="m2", thread="t-9")),
    ]
    assert [c.indices for c in build_clusters(signals)] == [(0, 1)]


def test_thread_ids_do_not_cross_source_kinds() -> None:
    """A Graph conversation id and a Teams chat id may collide as strings. They are not
    the same thread, and merging on that would be a silent false merge."""
    signals = [
        sig("a", ref(kind="mail", id_="m1", thread="X")),
        sig("b", ref(kind="chat", id_="c1", thread="X")),
    ]
    assert len(build_clusters(signals)) == 2


def test_subject_clusters_across_reply_prefixes() -> None:
    signals = [
        sig("confirm the renewal number", ref(id_="m1")),
        sig("chase the renewal number", ref(id_="m2")),
    ]
    lookup = {"m1": "Q3 vendor renewal", "m2": "Re: Fwd: Q3 Vendor Renewal"}
    assert [c.indices for c in build_clusters(signals, subject_lookup=lookup)] == [(0, 1)]


def test_generic_subject_does_not_cluster() -> None:
    signals = [
        sig("send the deck", ref(id_="m1")),
        sig("approve the budget", ref(id_="m2")),
    ]
    lookup = {"m1": "Quick question", "m2": "Quick question"}
    assert len(build_clusters(signals, subject_lookup=lookup)) == 2


def test_entity_key_clusters_across_sources() -> None:
    signals = [
        sig("PROJ-1421 needs a decision", ref(kind="mail", id_="m1")),
        sig("any movement on PROJ-1421?", ref(kind="chat", id_="c1")),
    ]
    assert [c.indices for c in build_clusters(signals)] == [(0, 1)]


def test_entity_key_in_an_excerpt_also_clusters() -> None:
    signals = [
        sig("needs a decision", ref(id_="m1", excerpt="blocked on PROJ-1421")),
        sig("chase the decision", ref(kind="chat", id_="c1", excerpt="PROJ-1421 still open")),
    ]
    assert len(build_clusters(signals)) == 1


def test_transitive_clustering() -> None:
    """A joins B by thread, B joins C by ticket, so all three are one cluster."""
    signals = [
        sig("confirm the number", ref(id_="m1", thread="t-1")),
        sig("PROJ-1421 blocks it", ref(id_="m2", thread="t-1")),
        sig("PROJ-1421 update", ref(kind="chat", id_="c1")),
    ]
    assert [c.indices for c in build_clusters(signals)] == [(0, 1, 2)]


def test_cluster_order_is_stable() -> None:
    """Ordering by earliest member is what keeps the pull request diff clean."""
    signals = [
        sig("alpha", ref(id_="m3")),
        sig("beta", ref(id_="m1")),
        sig("gamma", ref(id_="m2")),
    ]
    assert [c.indices for c in build_clusters(signals)] == [(0,), (1,), (2,)]


def test_clusters_partition_the_input_exactly() -> None:
    """Every signal appears in exactly one cluster. No loss, no duplication."""
    signals = [
        sig("PROJ-1 a", ref(id_="m1")),
        sig("PROJ-1 b", ref(id_="m2")),
        sig("unrelated", ref(id_="m3")),
        sig("also unrelated", ref(id_="m4")),
    ]
    clusters = build_clusters(signals)
    covered = [i for c in clusters for i in c.indices]
    assert sorted(covered) == list(range(len(signals)))
    assert len(covered) == len(set(covered))


def test_clusters_carry_the_evidence_that_joined_them() -> None:
    """When Stage B splits a cluster, the key that wrongly joined it must be visible."""
    signals = [
        sig("PROJ-1421 a", ref(id_="m1")),
        sig("PROJ-1421 b", ref(id_="m2")),
    ]
    assert "ticket:PROJ-1421" in build_clusters(signals)[0].keys


def test_cluster_signals_wrapper_returns_signals() -> None:
    signals = [sig("a", ref(id_="m1")), sig("b", ref(id_="m2"))]
    assert cluster_signals(signals) == [[signals[0]], [signals[1]]]


def test_signal_keys_include_message_identity() -> None:
    assert "message:mail:m1" in signal_keys(sig("a", ref(id_="m1")))


def test_clustering_is_deterministic_across_input_order() -> None:
    """Same signals, different order in, same partition out."""
    a = sig("PROJ-9 one", ref(id_="m1"))
    b = sig("PROJ-9 two", ref(id_="m2"))
    c = sig("unrelated", ref(id_="m3"))
    forward = {frozenset(g) for g in ([0, 1], [2])}
    result = {frozenset(cl.indices) for cl in build_clusters([a, b, c])}
    assert result == forward
    reordered = build_clusters([c, b, a])
    assert {len(cl.indices) for cl in reordered} == {1, 2}


# ======================================================================================
# the planted traps
# ======================================================================================


def test_trap_1_the_triple_collapses_to_one_cluster() -> None:
    """THE MONEY MOMENT.

    One ask arrives three ways: an email Tuesday, a Teams nudge Wednesday, and a line in
    a Thursday invite body. A single agent yields three to-dos. Stage A must hand Stage B
    one cluster of three, and every source must survive.
    """
    email = ref(kind="mail", id_="m1", thread="t-77", excerpt="confirm the renewal, PROJ-1421")
    chat = ref(kind="chat", id_="c1", excerpt="any luck on PROJ-1421?", offset_hours=24)
    invite = ref(kind="calendar", id_="e1", excerpt="bring the PROJ-1421 number", offset_hours=48)

    signals = [
        sig("confirm the Q3 vendor renewal number", email),
        sig("chase the renewal number", chat),
        sig("bring the renewal number to Thursday's meeting", invite, type_="meeting_prep"),
    ]

    clusters = build_clusters(signals, subject_lookup={"m1": "Q3 vendor renewal"})
    assert len(clusters) == 1, f"the triple did not collapse:\n{clusters}"
    assert clusters[0].indices == (0, 1, 2)

    merged_sources = {(s.kind, s.id) for i in clusters[0].indices for s in signals[i].sources}
    assert merged_sources == {("mail", "m1"), ("chat", "c1"), ("calendar", "e1")}


def test_trap_2_the_buried_commitment_is_its_own_cluster() -> None:
    """A commitment made inside a long thread must survive as an item.

    It shares the thread with the ask it answers, which is correct — but it must still be
    present, and Stage B is what decides whether it is one item or two.
    """
    thread = "t-42"
    signals = [
        sig(
            "send the revised numbers by Friday",
            ref(id_="m9", thread=thread, author="Kaan"),
            type_="commitment",
            due=NOW + timedelta(days=2),
        ),
        sig("confirm the vendor renewal", ref(id_="m1", thread=thread)),
    ]
    clusters = build_clusters(signals)
    assert len(clusters) == 1
    assert 0 in clusters[0].indices, "the buried commitment was dropped"


def test_trap_3_soon_ish_carries_no_deadline() -> None:
    """Enforced by the contract, not by the prompt. Restated here so the trap has a test."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        sig(
            "look at this soon-ish",
            ref(kind="chat", id_="c9"),
            ambiguous=True,
            due=NOW + timedelta(days=3),
        )

    ok = sig("look at this soon-ish", ref(kind="chat", id_="c9"), ambiguous=True)
    assert ok.due is None


def test_trap_4_urgent_phrasing_creates_no_clustering_evidence() -> None:
    """The polite trap. Tone is not a key; only deadlines, senders, and sources score."""
    signals = [
        sig("URGENT!! please read immediately", ref(id_="m1")),
        sig("ASAP - critical - action required", ref(id_="m2")),
    ]
    assert len(build_clusters(signals)) == 2


def test_trap_6_volume_does_not_over_merge() -> None:
    """Forty unrelated messages must yield forty clusters, not one soup."""
    signals = [sig(f"unrelated ask number {i}", ref(id_=f"m{i}")) for i in range(40)]
    assert len(build_clusters(signals)) == 40


def test_two_distinct_asks_sharing_a_subject_stay_separable() -> None:
    """Stage A groups them — that is intended, it optimises for recall — but the cluster
    keeps both, so Stage B has the material to split them."""
    signals = [
        sig("approve the vendor renewal budget", ref(id_="m1")),
        sig("cancel the vendor renewal entirely", ref(id_="m2")),
    ]
    lookup = {"m1": "Q3 vendor renewal", "m2": "Q3 vendor renewal"}
    clusters = build_clusters(signals, subject_lookup=lookup)
    assert len(clusters) == 1
    assert clusters[0].indices == (0, 1)
