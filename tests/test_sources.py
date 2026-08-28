"""Normalisation at the source boundary, plus the client's retry and paging behaviour.

Agents never see raw Graph JSON, so this is where the shape of the outside world stops.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from cos.graph.client import GraphClient, GraphError, StaticToken
from cos.models import MailMessage
from cos.settings import WindowSettings
from cos.sources import calendar as calendar_source
from cos.sources import mail as mail_source
from cos.sources import refs
from cos.sources.normalise import html_to_text, parse_timestamp
from cos.sources.window import add_business_days, resolve
from tests.conftest import FixtureMiss, StubTransport

TZ = ZoneInfo("America/Toronto")


def client(stub: StubTransport) -> GraphClient:
    return GraphClient(auth=StaticToken(), transport=stub, sleep=lambda _: None)


# ======================================================================================
# html to text
# ======================================================================================


def test_html_is_stripped_once_at_the_boundary() -> None:
    html = "<div><p>Hi Priya,</p><p>Confirm the <b>renewal</b> number?</p></div>"
    assert html_to_text(html) == "Hi Priya,\n\nConfirm the renewal number?"


def test_style_and_script_blocks_are_removed_entirely() -> None:
    """Otherwise a tracking blob becomes tokens the extraction agent pays to read."""
    html = "<style>.x{color:red}</style><p>Real content</p><script>track()</script>"
    assert html_to_text(html) == "Real content"


def test_entities_are_decoded() -> None:
    assert html_to_text("<p>Q3&nbsp;renewal &amp; budget</p>") == "Q3 renewal & budget"


def test_blank_run_collapse() -> None:
    assert "\n\n\n" not in html_to_text("<p>a</p><br><br><br><p>b</p>")


def test_empty_html() -> None:
    assert html_to_text("") == ""


# ======================================================================================
# timestamps
# ======================================================================================


def test_graph_z_suffix_parses_to_aware() -> None:
    parsed = parse_timestamp("2026-08-25T09:14:00Z")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed == datetime(2026, 8, 25, 9, 14, tzinfo=UTC)


def test_subsecond_precision_beyond_six_digits_parses() -> None:
    """Graph calendar values carry more precision than fromisoformat accepts."""
    assert parse_timestamp("2026-08-25T09:14:00.1234567Z") is not None


def test_missing_timestamp_is_none() -> None:
    assert parse_timestamp(None) is None


# ======================================================================================
# mail normalisation
# ======================================================================================


def mail_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "AAMk-1",
        "conversationId": "AAQk-77",
        "internetMessageId": "<abc@contoso>",
        "subject": "Q3 vendor renewal",
        "from": {"emailAddress": {"name": "Priya Raman", "address": "priya@demo.example"}},
        "toRecipients": [{"emailAddress": {"address": "kaan@demo.example"}}],
        "ccRecipients": [],
        "receivedDateTime": "2026-08-25T09:14:00Z",
        "bodyPreview": "Can you confirm the renewal number?",
        "body": {"contentType": "html", "content": "<p>Can you confirm the renewal number?</p>"},
        "webLink": "https://outlook.office.com/mail/id/AAMk-1",
    }
    payload.update(overrides)
    return payload


def test_mail_normalises_to_the_internal_model() -> None:
    message = mail_source.normalise(mail_payload())
    assert isinstance(message, MailMessage)
    assert message.conversation_id == "AAQk-77"
    assert message.from_name == "Priya Raman"
    assert message.body_text == "Can you confirm the renewal number?"
    assert message.received_at.tzinfo is not None


def test_message_without_a_timestamp_is_dropped() -> None:
    """No timestamp means no provenance, and provenance is not optional."""
    assert mail_source.normalise(mail_payload(receivedDateTime=None)) is None


def test_drafts_are_dropped() -> None:
    assert mail_source.normalise(mail_payload(isDraft=True)) is None


def test_operator_authored_mail_is_flagged() -> None:
    """This is what makes the buried commitment findable at all."""
    payload = mail_payload(
        **{"from": {"emailAddress": {"address": "Kaan@Demo.Example", "name": "Kaan"}}}
    )
    message = mail_source.normalise(payload, operator_address="kaan@demo.example")
    assert message is not None and message.is_from_operator


def test_mail_from_someone_else_is_not_flagged() -> None:
    message = mail_source.normalise(mail_payload(), operator_address="kaan@demo.example")
    assert message is not None and not message.is_from_operator


def test_body_falls_back_to_preview_when_body_is_absent() -> None:
    message = mail_source.normalise(mail_payload(body=None))
    assert message is not None and message.body_text == "Can you confirm the renewal number?"


# ======================================================================================
# calendar normalisation
# ======================================================================================


def event_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "AAEv-3",
        "subject": "Vendor sync",
        "start": {"dateTime": "2026-08-27T14:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-08-27T15:00:00.0000000", "timeZone": "UTC"},
        "organizer": {"emailAddress": {"address": "priya@demo.example"}},
        "attendees": [{"emailAddress": {"address": "kaan@demo.example"}}],
        "body": {"contentType": "html", "content": "<p>Bring the renewal number.</p>"},
        "isAllDay": False,
        "isCancelled": False,
        "webLink": "https://outlook.office.com/calendar/item/AAEv-3",
    }
    payload.update(overrides)
    return payload


def test_calendar_normalises_and_keeps_the_invite_body() -> None:
    """The invite body is one leg of the triple. Dropping it loses a source."""
    event = calendar_source.normalise(event_payload())
    assert event is not None
    assert event.body_text == "Bring the renewal number."
    assert event.organizer == "priya@demo.example"


def test_event_without_start_is_dropped() -> None:
    assert calendar_source.normalise(event_payload(start={})) is None


def test_event_ending_before_it_starts_is_dropped_not_raised() -> None:
    """A bad event must not take the whole run down with it."""
    payload = event_payload(end={"dateTime": "2026-08-27T13:00:00.0000000"})
    assert calendar_source.normalise(payload) is None


# ======================================================================================
# provenance
# ======================================================================================


def test_source_ref_from_mail_carries_the_deep_link() -> None:
    message = mail_source.normalise(mail_payload())
    assert message is not None
    ref = refs.from_mail(message)
    assert ref.kind == "mail"
    assert ref.permalink == "https://outlook.office.com/mail/id/AAMk-1"
    assert ref.thread_id == "AAQk-77"


def test_excerpt_truncates_on_a_word_boundary() -> None:
    """Mid-word truncation looks like corruption in the pull request body."""
    text = "word " * 100
    value = refs.excerpt(text)
    assert len(value) <= 240
    assert value.endswith("…")
    assert "wor…" not in value


def test_excerpt_leaves_short_text_alone() -> None:
    assert refs.excerpt("  short   text ") == "short text"


def test_source_ref_from_event_uses_the_start_time() -> None:
    event = calendar_source.normalise(event_payload())
    assert event is not None
    assert refs.from_event(event).timestamp == event.start


# ======================================================================================
# window
# ======================================================================================


def test_monday_reaches_back_to_friday() -> None:
    """A fixed 24 hours silently drops the weekend — when a brief matters most."""
    window = resolve(WindowSettings(), now=datetime(2026, 8, 24, 9, 0, tzinfo=TZ))
    assert window.start.weekday() == 4
    assert window.start.hour == 0
    assert window.hours > 24


def test_midweek_is_an_ordinary_lookback() -> None:
    window = resolve(WindowSettings(), now=datetime(2026, 8, 25, 9, 0, tzinfo=TZ))
    assert window.hours == 24


def test_an_explicit_window_is_never_extended() -> None:
    """If the operator asked for six hours, they meant six hours."""
    window = resolve(WindowSettings(), now=datetime(2026, 8, 24, 9, 0, tzinfo=TZ), lookback_hours=6)
    assert window.hours == 6


def test_calendar_lookahead_covers_the_business_days_requested() -> None:
    window = resolve(WindowSettings(), now=datetime(2026, 8, 28, 9, 0, tzinfo=TZ))
    assert window.calendar_start.date() == datetime(2026, 8, 28).date()
    # Friday plus two business days is Tuesday, so the exclusive end is Wednesday.
    assert window.calendar_end.date() == datetime(2026, 9, 2).date()


def test_add_business_days_skips_the_weekend() -> None:
    assert add_business_days(datetime(2026, 8, 28).date(), 1) == datetime(2026, 8, 31).date()


# ======================================================================================
# client behaviour
# ======================================================================================


def test_paging_follows_next_link(stub: StubTransport) -> None:
    stub.add({"value": [{"id": "1"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"})
    stub.add({"value": [{"id": "2"}]})
    items = list(client(stub).paged("/me/messages"))
    assert [i["id"] for i in items] == ["1", "2"]


def test_paging_stops_at_max_items(stub: StubTransport) -> None:
    """An unbounded page-follow over a busy mailbox is how a demo becomes a wait."""
    stub.add({"value": [{"id": str(n)} for n in range(50)], "@odata.nextLink": "https://x/next"})
    assert len(list(client(stub).paged("/me/messages", max_items=10))) == 10


def test_throttling_is_retried(stub: StubTransport) -> None:
    stub.responses.append((429, {"error": "throttled"}))
    stub.add({"value": []})
    assert client(stub).get("/me/messages") == {"value": []}
    assert len(stub.requests) == 2


def test_retry_after_is_honoured(stub: StubTransport) -> None:
    """Ignoring Graph's Retry-After makes throttling worse, not better."""
    delays: list[float] = []
    stub.responses.append((429, {"error": "throttled"}))
    stub.add({"ok": True})
    c = GraphClient(auth=StaticToken(), transport=stub, sleep=delays.append)
    # StubTransport cannot set headers, so this asserts the shape: one backoff, bounded.
    c.get("/me/messages")
    assert len(delays) == 1
    assert 0 < delays[0] <= 30


def test_a_client_error_is_not_retried(stub: StubTransport) -> None:
    stub.responses.append((404, {"error": "not found"}))
    with pytest.raises(GraphError) as exc:
        client(stub).get("/me/messages/missing")
    assert exc.value.status == 404
    assert len(stub.requests) == 1


def test_persistent_throttling_eventually_raises(stub: StubTransport) -> None:
    for _ in range(10):
        stub.responses.append((429, {"error": "throttled"}))
    with pytest.raises(GraphError) as exc:
        client(stub).get("/me/messages")
    assert exc.value.status == 429


def test_a_missing_fixture_fails_loudly(replay: Any) -> None:
    """An accidental live call in CI must be a test failure, never a silent pass."""
    c = GraphClient(auth=StaticToken(), transport=replay, sleep=lambda _: None)
    with pytest.raises(FixtureMiss, match="no recorded response"):
        c.get("/me/messages")


# ======================================================================================
# chat normalisation — implemented and unused, so it must not rot while it waits
# ======================================================================================


def chat_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "1724000000000",
        "createdDateTime": "2026-08-25T14:02:00Z",
        "from": {
            "user": {
                "displayName": "Priya Raman",
                "email": "priya@demo.example",
                "userPrincipalName": "priya@demo.example",
            }
        },
        "body": {"contentType": "html", "content": "<p>any luck on that renewal figure?</p>"},
        "webLink": "https://teams.microsoft.com/l/message/19:abc/1724000000000",
    }
    payload.update(overrides)
    return payload


def test_chat_normalises_and_strips_markup() -> None:
    from cos.sources import chat as chat_source

    message = chat_source.normalise(chat_payload(), chat_id="19:abc")
    assert message is not None
    assert message.body_text == "any luck on that renewal figure?"
    assert message.chat_id == "19:abc"


def test_a_system_message_with_no_user_is_dropped() -> None:
    """Joins, renames, and reactions are not asks."""
    from cos.sources import chat as chat_source

    assert chat_source.normalise(chat_payload(**{"from": {}}), chat_id="19:abc") is None


def test_an_empty_chat_message_is_dropped() -> None:
    from cos.sources import chat as chat_source

    payload = chat_payload(body={"contentType": "html", "content": "<p></p>"})
    assert chat_source.normalise(payload, chat_id="19:abc") is None


def test_preceding_context_is_carried() -> None:
    """Without it there is no honest way to resolve 'can you take a look?'."""
    from cos.sources import chat as chat_source

    message = chat_source.normalise(
        chat_payload(), chat_id="19:abc", preceding=["Sam Ito: pushed the deck"]
    )
    assert message is not None
    assert message.preceding_context == ["Sam Ito: pushed the deck"]


def test_operator_authored_chat_is_flagged() -> None:
    from cos.sources import chat as chat_source

    message = chat_source.normalise(
        chat_payload(), chat_id="19:abc", operator_address="PRIYA@demo.example"
    )
    assert message is not None and message.is_from_operator
