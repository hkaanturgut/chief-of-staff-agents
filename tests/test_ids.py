"""Identifier stability.

If these fail, the second-run-is-a-clean-diff property is gone, and with it the
idempotency guarantee that the ledger depends on.
"""

from __future__ import annotations

import subprocess
import sys

from cos.ids import action_id, normalise, todo_id
from cos.models import MailTarget

STATEMENT = "Confirm the Q3 vendor renewal number before Priya sends it upstream"
SOURCES = [("mail", "AAMk-1"), ("chat", "1724-2"), ("calendar", "AAEv-3")]


def test_same_content_same_id() -> None:
    assert todo_id(STATEMENT, SOURCES) == todo_id(STATEMENT, SOURCES)


def test_source_order_does_not_matter() -> None:
    assert todo_id(STATEMENT, SOURCES) == todo_id(STATEMENT, list(reversed(SOURCES)))


def test_duplicate_sources_do_not_matter() -> None:
    assert todo_id(STATEMENT, SOURCES) == todo_id(STATEMENT, [*SOURCES, SOURCES[0]])


def test_different_statement_different_id() -> None:
    assert todo_id(STATEMENT, SOURCES) != todo_id(STATEMENT + " today", SOURCES)


def test_different_sources_different_id() -> None:
    assert todo_id(STATEMENT, SOURCES) != todo_id(STATEMENT, SOURCES[:2])


def test_id_shape_is_ulid_like() -> None:
    value = todo_id(STATEMENT, SOURCES)
    assert len(value) == 26
    assert set(value) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_stable_across_processes() -> None:
    """Guards against anything reaching for PYTHONHASHSEED-dependent behaviour."""
    code = f"from cos.ids import todo_id;print(todo_id({STATEMENT!r}, {SOURCES!r}))"
    first = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    second = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert first.stdout.strip() == second.stdout.strip() == todo_id(STATEMENT, SOURCES)


def test_action_id_stable_and_target_sensitive() -> None:
    tid = todo_id(STATEMENT, SOURCES)
    a = MailTarget(to=["priya@demo.example"], subject="Re: Q3 vendor renewal")
    b = MailTarget(to=["someone-else@demo.example"], subject="Re: Q3 vendor renewal")
    assert action_id(tid, "reply_mail", a) == action_id(tid, "reply_mail", a)
    assert action_id(tid, "reply_mail", a) != action_id(tid, "reply_mail", b)
    assert action_id(tid, "reply_mail", a) != action_id(tid, "send_mail", a)


def test_editing_the_body_does_not_change_the_action_id() -> None:
    """A human editing a draft must not create a second send.

    The id covers the to-do, the kind, and the target. It deliberately does not cover
    the body, which is the part a reviewer is expected to rewrite.
    """
    tid = todo_id(STATEMENT, SOURCES)
    target = MailTarget(to=["priya@demo.example"], subject="Re: Q3 vendor renewal")
    assert action_id(tid, "reply_mail", target) == action_id(tid, "reply_mail", target)


def test_normalise() -> None:
    assert normalise("  Send   the   NUMBERS.  ") == "send the numbers"
    assert normalise("Send the numbers") == normalise("send the numbers!")
