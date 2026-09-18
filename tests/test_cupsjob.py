"""Known-answer tests for the IPP client.

The two responses below are real, captured from the cupsd that exposed
this bug rather than written by hand. They live here as literals and not
under tests/fixtures/, which is gitignored because it holds other
people's copyrighted mail: these are a few dozen bytes of job counters
with nothing private in them, and a contributor with no CUPS at all
should still be able to run these tests.
"""

import io
import struct

import pytest

from newsprint.cupsjob import (
    IppError,
    JobState,
    decode_response,
    encode_request,
    fetch,
)

# Job 8727, the 2026-09-18 run: job-state 9 (completed), 0x43 = 67
# impressions, 0x21 = 33 sheets, of a document that had 107 sides.
SHORT_JOB = (
    b"\x02\x00\x00\x00\x00\x00\x00\x01"
    b"\x01"
    b"G\x00\x12attributes-charset\x00\x05utf-8"
    b"H\x00\x1battributes-natural-language\x00\x02en"
    b"\x02"
    b"#\x00\tjob-state\x00\x04\x00\x00\x00\t"
    b"D\x00\x11job-state-reasons\x00\x18processing-to-stop-point"
    b"!\x00\x19job-impressions-completed\x00\x04\x00\x00\x00C"
    b"!\x00\x1ajob-media-sheets-completed\x00\x04\x00\x00\x00!"
    b"\x03"
)

# An IPP error, which arrives as a perfectly ordinary HTTP 200.
NO_SUCH_JOB = (
    b"\x02\x00\x04\x06\x00\x00\x00\x01"
    b"\x01"
    b"G\x00\x12attributes-charset\x00\x05utf-8"
    b"H\x00\x1battributes-natural-language\x00\x02en"
    b"A\x00\x0estatus-message\x00\x1aJob #99999 does not exist."
    b"\x03"
)


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
    state = decode_response(SHORT_JOB)
    assert state == JobState(
        state=9,
        reasons=("processing-to-stop-point",),
        impressions=67,
        sheets=33,
    )


def test_a_completed_state_is_not_proof_of_a_full_print() -> None:
    """Guards the distinction the bug turned on: succeeded is about how
    CUPS ended the job, and says nothing about how much of it printed."""
    state = decode_response(SHORT_JOB)
    assert state.succeeded is True
    assert state.terminal is True


def test_a_missing_job_raises_with_the_servers_own_message() -> None:
    """CUPS reports this as HTTP 200 with an IPP error status, so nothing
    below the IPP layer will notice it."""
    with pytest.raises(IppError, match="Job #99999 does not exist"):
        decode_response(NO_SUCH_JOB)


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
        decode_response(SHORT_JOB[:40])


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
        return SHORT_JOB

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
        yield io.BytesIO(SHORT_JOB)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert cupsjob._post(b"body") == SHORT_JOB
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


def test_the_first_error_status_is_still_an_error() -> None:
    """0x0100 is the boundary: everything below it is a success class, and
    0x0100 itself is client-error-bad-request. Treating it as success
    would report a rejected request as a finished job."""
    with pytest.raises(IppError, match="0x0100"):
        decode_response(response(integer("job-state", 9), status=0x0100))


def test_a_header_with_no_attributes_is_not_truncated() -> None:
    """Exactly eight bytes is a complete header that simply carries
    nothing - a different failure from a message cut in half, and it must
    be reported as the missing job-state it is."""
    with pytest.raises(IppError, match="job-state"):
        decode_response(struct.pack(">HHI", 0x0200, 0x0000, 1))


def test_the_status_message_is_found_by_name_and_type() -> None:
    """Other text attributes travel in the same group. Matching on the tag
    alone would report detailed-status-message, which is diagnostic noise
    rather than the reason the request failed."""
    text = struct.pack(">BH", 0x41, len(b"detailed-status-message"))
    text += b"detailed-status-message" + struct.pack(">H", len(b"debug noise"))
    text += b"debug noise"
    message = struct.pack(">BH", 0x41, len(b"status-message"))
    message += b"status-message" + struct.pack(">H", len(b"the real reason"))
    message += b"the real reason"
    with pytest.raises(IppError, match="the real reason"):
        decode_response(response(text, message, status=0x0406))


def test_every_group_delimiter_is_skipped() -> None:
    """CUPS opens an unsupported-attributes group with tag 0x05, the
    highest delimiter. Reading that as a value tag would consume the next
    bytes as a name and desynchronise the whole walk."""
    body = struct.pack(">HHI", 0x0200, 0x0000, 1)
    body += b"\x02" + integer("job-state", 9)
    body += b"\x05" + keyword("job-hold-until", "no-hold")
    body += b"\x02" + integer("job-impressions-completed", 7)
    body += b"\x03"
    assert decode_response(body).impressions == 7


# The exact request newsprint sends, byte for byte. This one was verified
# against a live cupsd, which is the only thing that can judge whether an
# attribute name, a tag or a length prefix is right: a server rejects the
# whole request rather than reporting which field offended it, so a unit
# test that checked the pieces loosely would pass on a request no printer
# would ever answer.
GOLDEN_REQUEST = (
    b"\x02\x00\x00\t\x00\x00\x00\x01"
    b"\x01"
    b"G\x00\x12attributes-charset\x00\x05utf-8"
    b"H\x00\x1battributes-natural-language\x00\x02en"
    b"E\x00\x07job-uri\x00\x1dipp://localhost:631/jobs/8727"
    b"D\x00\x14requested-attributes\x00\tjob-state"
    b"D\x00\x00\x00\x11job-state-reasons"
    b"D\x00\x00\x00\x19job-impressions-completed"
    b"D\x00\x00\x00\x1ajob-media-sheets-completed"
    b"\x03"
)


def test_the_request_is_exactly_this() -> None:
    assert encode_request(8727) == GOLDEN_REQUEST


def test_reasons_are_matched_by_name_and_type() -> None:
    """Other keywords share the group - job-hold-until among them. Matching
    on the tag alone would file them all as state reasons."""
    state = decode_response(
        response(
            integer("job-state", 9),
            keyword("job-hold-until", "no-hold"),
            keyword("job-state-reasons", "job-completed-successfully"),
        )
    )
    assert state.reasons == ("job-completed-successfully",)
