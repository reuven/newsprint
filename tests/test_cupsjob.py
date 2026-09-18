"""Known-answer tests for the IPP client.

The fixtures are real responses from the CUPS that exposed this bug, not
hand-written bytes: tests/fixtures/ipp/short-job.ipp is job 8727, the
2026-09-18 run that put 67 of 107 sides on paper and was still reported
as completed.
"""

import io
import struct
from pathlib import Path

import pytest

from newsprint.cupsjob import (
    IppError,
    JobState,
    decode_response,
    encode_request,
    fetch,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ipp"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def response(*attributes: bytes, status: int = 0x0000) -> bytes:
    """A minimal well-formed response: header, job-attributes group, end."""
    return (
        struct.pack(">HHI", 0x0200, status, 1)
        + b"\x02"
        + b"".join(attributes)
        + b"\x03"
    )


def integer(name: str, value: int) -> bytes:
    encoded = name.encode()
    return (
        struct.pack(">BH", 0x21, len(encoded)) + encoded + struct.pack(">HI", 4, value)
    )


def keyword(name: str, value: str) -> bytes:
    encoded, text = name.encode(), value.encode()
    return (
        struct.pack(">BH", 0x44, len(encoded))
        + encoded
        + struct.pack(">H", len(text))
        + text
    )


def additional(value: str) -> bytes:
    """A second value for the attribute just written: no name, per RFC 8010."""
    text = value.encode()
    return struct.pack(">BHH", 0x44, 0, len(text)) + text


# --- decoding ------------------------------------------------------------


def test_decodes_the_job_that_exposed_this() -> None:
    """The whole point of the feature, against the real bytes. CUPS calls
    this job completed; only the impression count says otherwise."""
    state = decode_response(fixture("short-job.ipp"))
    assert state == JobState(
        state=9,
        reasons=("processing-to-stop-point",),
        impressions=67,
        sheets=33,
    )


def test_a_completed_state_is_not_proof_of_a_full_print() -> None:
    """Guards the distinction the bug turned on: succeeded is about how
    CUPS ended the job, and says nothing about how much of it printed."""
    state = decode_response(fixture("short-job.ipp"))
    assert state.succeeded is True
    assert state.terminal is True


def test_a_missing_job_raises_with_the_servers_own_message() -> None:
    """CUPS reports this as HTTP 200 with an IPP error status, so nothing
    below the IPP layer will notice it."""
    with pytest.raises(IppError, match="Job #99999 does not exist"):
        decode_response(fixture("no-such-job.ipp"))


def test_an_error_without_a_message_still_raises() -> None:
    with pytest.raises(IppError, match="0x0400"):
        decode_response(response(status=0x0400))


def test_reasons_collects_every_value() -> None:
    """job-state-reasons is a 1setOf; the extra values arrive as nameless
    continuations of the attribute before them."""
    state = decode_response(
        response(
            integer("job-state", 9),
            keyword("job-state-reasons", "job-completed-successfully"),
            additional("job-canceled-by-user"),
        )
    )
    assert state.reasons == ("job-completed-successfully", "job-canceled-by-user")


def test_absent_counters_read_as_zero() -> None:
    """A job that has not started has no impressions attribute at all,
    which is a count of zero rather than a malformed response."""
    state = decode_response(response(integer("job-state", 3)))
    assert (state.impressions, state.sheets, state.reasons) == (0, 0, ())


def test_unknown_attributes_are_skipped() -> None:
    state = decode_response(
        response(
            keyword("job-name", "something.pdf"),
            integer("job-state", 9),
            integer("job-priority", 50),
            integer("job-impressions-completed", 12),
        )
    )
    assert state.impressions == 12


def test_a_response_without_a_state_is_an_error() -> None:
    """Better to fail loudly than to invent a state and retire the mail."""
    with pytest.raises(IppError, match="job-state"):
        decode_response(response(integer("job-impressions-completed", 12)))


def test_a_truncated_response_is_an_error() -> None:
    with pytest.raises(IppError, match="truncated"):
        decode_response(fixture("short-job.ipp")[:40])


def test_a_response_too_short_for_a_header_is_an_error() -> None:
    with pytest.raises(IppError, match="truncated"):
        decode_response(b"\x02\x00")


@pytest.mark.parametrize(
    ("state", "terminal", "succeeded"),
    [
        (3, False, False),  # pending
        (4, False, False),  # pending-held
        (5, False, False),  # processing
        (6, False, False),  # processing-stopped
        (7, True, False),  # canceled
        (8, True, False),  # aborted
        (9, True, True),  # completed
    ],
)
def test_terminal_and_succeeded_follow_the_ipp_enum(
    state: int, terminal: bool, succeeded: bool
) -> None:
    job = JobState(state=state, reasons=(), impressions=0, sheets=0)
    assert (job.terminal, job.succeeded) == (terminal, succeeded)


# --- encoding ------------------------------------------------------------


def test_request_is_a_get_job_attributes_for_the_named_job() -> None:
    body = encode_request(8727)
    assert body[:8] == struct.pack(">HHI", 0x0200, 0x0009, 1)
    assert b"ipp://localhost:631/jobs/8727" in body
    assert body.endswith(b"\x03")


def test_request_puts_charset_and_language_first() -> None:
    """RFC 8010 fixes this order, and CUPS rejects a request that gets it
    wrong - a failure that would only ever show up against a real server."""
    body = encode_request(1)
    assert body[8] == 0x01  # operation-attributes-tag
    assert (
        body.index(b"attributes-charset")
        < body.index(b"attributes-natural-language")
        < body.index(b"job-uri")
    )


def test_request_asks_only_for_what_is_needed() -> None:
    body = encode_request(1)
    assert body.count(b"requested-attributes") == 1
    for wanted in (
        b"job-state",
        b"job-state-reasons",
        b"job-impressions-completed",
        b"job-media-sheets-completed",
    ):
        assert wanted in body


# --- fetch ---------------------------------------------------------------


def test_fetch_posts_the_request_and_decodes_the_reply() -> None:
    sent: list[bytes] = []

    def transport(body: bytes) -> bytes:
        sent.append(body)
        return fixture("short-job.ipp")

    assert fetch(8727, transport=transport).impressions == 67
    assert sent == [encode_request(8727)]


def test_a_message_that_never_ends_is_still_read() -> None:
    """A response whose end-of-attributes tag is missing: the walk has to
    stop at the buffer's end rather than run off it."""
    body = response(integer("job-state", 9), integer("job-impressions-completed", 5))
    assert decode_response(body[:-1]).impressions == 5


def test_a_value_shorter_than_it_claims_is_an_error() -> None:
    """The length header says four bytes and two arrive. Reading that as
    the number 5 would be inventing a print run out of a damaged reply."""
    body = response(integer("job-state", 9), integer("job-impressions-completed", 5))
    with pytest.raises(IppError, match="truncated"):
        decode_response(body[:-3])


def test_post_sends_ipp_to_the_local_cups(monkeypatch) -> None:
    import contextlib
    import urllib.request

    from newsprint import cupsjob

    seen: list[urllib.request.Request] = []

    @contextlib.contextmanager
    def fake_urlopen(request, timeout):
        seen.append(request)
        yield io.BytesIO(fixture("short-job.ipp"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert cupsjob._post(b"body") == fixture("short-job.ipp")
    assert seen[0].full_url == cupsjob.SERVER
    assert seen[0].data == b"body"
    assert seen[0].headers["Content-type"] == cupsjob.CONTENT_TYPE


def test_an_unreachable_cups_becomes_an_ipp_error(monkeypatch) -> None:
    """urllib raises half a dozen different things when a socket fails;
    the caller only needs to know the state could not be read."""
    import urllib.request

    from newsprint import cupsjob

    def refuse(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(IppError, match="could not reach CUPS"):
        cupsjob._post(b"body")
