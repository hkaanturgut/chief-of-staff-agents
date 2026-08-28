"""The corpus itself is an artifact worth testing.

If a trap stops being present in the corpus, the evaluation that depends on it starts
passing for the wrong reason.
"""

from __future__ import annotations

from cos.corpus import load


def test_corpus_loads_and_validates() -> None:
    bundle, now, operator = load()
    assert bundle.total == 41
    assert operator == "kaan@demo.example"
    assert now.tzinfo is not None


def test_trap_1_the_triple_is_present_across_three_channels() -> None:
    bundle, _, _ = load()
    mail_hit = [m for m in bundle.mail if "PROJ-1421" in m.body_text or "PROJ-1421" in m.subject]
    chat_hit = [c for c in bundle.chat if "PROJ-1421" in c.body_text]
    event_hit = [e for e in bundle.events if "PROJ-1421" in e.body_text]
    assert mail_hit and chat_hit and event_hit, "the triple must span all three sources"


def test_trap_2_the_buried_commitment_is_from_the_operator_and_deep_in_a_thread() -> None:
    bundle, _, _ = load()
    thread = [m for m in bundle.mail if m.conversation_id == "t-budget"]
    assert len(thread) >= 5, "shallow threads do not bury anything"
    commitment = [m for m in thread if m.is_from_operator and "by Friday" in m.body_text]
    assert len(commitment) == 1
    assert thread.index(commitment[0]) > 0, "it must not be the first message"


def test_trap_3_the_invented_deadline_bait_is_present() -> None:
    bundle, _, _ = load()
    assert any("soon-ish" in c.body_text for c in bundle.chat)
    assert not any(
        word in c.body_text.lower() for c in bundle.chat for word in ("by friday", "by monday")
    ), "the chat bait must not contain a real date"


def test_trap_4_the_polite_trap_reads_urgent_and_asks_nothing() -> None:
    bundle, _, _ = load()
    loud = [m for m in bundle.mail if "URGENT" in m.subject.upper()]
    assert loud
    assert all(
        "no action" in m.body_text.lower() or "no response" in m.body_text.lower() for m in loud
    )


def test_trap_5_a_high_risk_item_involves_money_and_an_external_party() -> None:
    bundle, _, _ = load()
    money = [m for m in bundle.mail if "CAD" in m.body_text]
    assert money
    # Substring matching would call meridian-demo.example internal. Compare domains.
    assert any(m.from_address.rsplit("@", 1)[1] != "demo.example" for m in money)


def test_trap_6_volume_is_enough_that_manual_triage_is_tedious() -> None:
    bundle, _, _ = load()
    assert 25 <= len(bundle.mail) <= 40


def test_the_split_case_shares_a_thread_with_a_different_ask() -> None:
    """Stage A will group these; Stage B has to pull them apart."""
    bundle, _, _ = load()
    renewal = [m for m in bundle.mail if m.conversation_id == "t-renewal"]
    assert len(renewal) >= 3
    assert any("cancel" in m.body_text.lower() for m in renewal)


def test_every_corpus_message_is_fictional() -> None:
    """Nothing real may reach a projector or a public repository."""
    bundle, _, _ = load()
    allowed = ("demo.example", "northwind-demo.example", "meridian-demo.example")
    for message in bundle.mail:
        assert message.from_address.endswith(allowed), message.from_address
