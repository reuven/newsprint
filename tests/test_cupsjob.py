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
    PrinterJob,
    decode_device_uri,
    decode_jobs,
    decode_response,
    device_uri,
    encode_device_uri_request,
    encode_jobs_request,
    encode_request,
    fetch,
    http_url,
    printer_jobs,
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
    def fake_urlopen(request, timeout, context=None):
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

    def refuse(request, timeout, context=None):
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


# --- the queue's device, and the printer's own jobs ----------------------
#
# CUPS counts a side as done when it has *sent* it, so its
# job-impressions-completed runs ahead of paper by whatever the printer
# has buffered: on 2026-10-02 CUPS said 66 of 116 and the printer, which
# had run out of memory and cancelled the job, said 60. Only the printer
# knows what reached paper, so these ask it directly.
#
# The three replies below are real, captured from cupsd and from the
# Brother that cancelled that job. Two values are substituted, because
# this repository is public: the packet's file name and the printer's
# uuid. Their lengths are adjusted to match; every byte of structure is
# as received.

DEVICE_URI = (
    "dnssd://Brother%20MFC-L2700DW%20series._ipp._tcp.local./"
    "?uuid=00000000-0000-1000-8000-000000000000"
)

DEVICE_URI_REPLY = (
    b"\x02\x00\x00\x00\x00\x00\x00\x01"
    b"\x01"
    b"G\x00\x12attributes-charset\x00\x05utf-8"
    b"H\x00\x1battributes-natural-language\x00\x02en"
    b"\x04"
    b"E\x00\ndevice-uri"
    + struct.pack(">H", len(DEVICE_URI))
    + DEVICE_URI.encode()
    + b"\x03"
)

# Get-Jobs, which-jobs=completed, from the printer. Note three things the
# decoder has to cope with, none of which CUPS does: an empty
# natural-language, status 0x0001 (it ignored an attribute it does not
# support), and that attribute echoed back in an unsupported-attributes
# group - this printer has no job-media-sheets-completed. The job name is
# nameWithLanguage, a language and a text packed into one value.
PRINTER_COMPLETED = (
    b"\x02\x00\x00\x01\x00\x00\x00\x01"
    b"\x01"
    b"G\x00\x12attributes-charset\x00\x05utf-8"
    b"H\x00\x1battributes-natural-language\x00\x00"
    b"\x05"
    b"D\x00\x14requested-attributes\x00\x1ajob-media-sheets-completed"
    b"\x02"
    b"!\x00\x06job-id\x00\x04\x00\x00'\xe3"
    b"6\x00\x08job-name\x00#\x00\x05en-us\x00\x1apacket-2026-10-02-1227.pdf"
    b"#\x00\tjob-state\x00\x04\x00\x00\x00\t"
    b"D\x00\x11job-state-reasons\x00\x1ajob-completed-successfully"
    b"!\x00\x19job-impressions-completed\x00\x04\x00\x00\x00\x10"
    b"\x03"
)

# Get-Jobs, which-jobs=not-completed, with nothing printing: no job groups.
PRINTER_IDLE = (
    b"\x02\x00\x00\x00\x00\x00\x00\x01"
    b"\x01"
    b"G\x00\x12attributes-charset\x00\x05utf-8"
    b"H\x00\x1battributes-natural-language\x00\x00"
    b"\x03"
)


def name_without_language(name: str, value: str) -> bytes:
    """job-name as CUPS sends it: tag 0x42, plain text."""
    encoded, text = name.encode(), value.encode()
    return (
        struct.pack(">BH", 0x42, len(encoded))
        + encoded
        + struct.pack(">H", len(text))
        + text
    )


def jobs_response(*jobs: bytes, status: int = 0x0000) -> bytes:
    """A Get-Jobs reply: one job-attributes group per job."""
    return (
        struct.pack(">HHI", 0x0200, status, 1)
        + b"".join(b"\x02" + job for job in jobs)
        + b"\x03"
    )


def test_the_device_uri_is_read_from_cupss_reply() -> None:
    assert decode_device_uri(DEVICE_URI_REPLY) == DEVICE_URI


def test_a_reply_without_a_device_uri_is_an_error() -> None:
    with pytest.raises(IppError, match="device-uri"):
        decode_device_uri(PRINTER_IDLE)


def test_a_refused_device_uri_request_is_an_error() -> None:
    with pytest.raises(IppError, match="0x0406"):
        decode_device_uri(response(status=0x0406))


def test_the_device_uri_request_asks_the_queue_for_just_that() -> None:
    body = encode_device_uri_request("Brother_MFC_L2700DW_series")
    assert body[:8] == struct.pack(">HHI", 0x0200, 0x000B, 1)
    assert b"ipp://localhost/printers/Brother_MFC_L2700DW_series" in body
    assert body.count(b"requested-attributes") == 1
    assert b"device-uri" in body
    assert body.index(b"attributes-charset") < body.index(b"printer-uri")


def test_device_uri_posts_to_the_queue_on_the_local_cups() -> None:
    sent: list[tuple[str, bytes]] = []

    def poster(url: str, body: bytes) -> bytes:
        sent.append((url, body))
        return DEVICE_URI_REPLY

    assert device_uri("Q", poster=poster) == DEVICE_URI
    assert sent == [("http://localhost:631/printers/Q", encode_device_uri_request("Q"))]


def test_the_printers_own_job_is_decoded_from_the_real_reply() -> None:
    """The record that tells the truth about a short print. Sheets read as
    zero because this printer does not report them at all."""
    assert decode_jobs(PRINTER_COMPLETED) == [
        PrinterJob(
            id=10211,
            name="packet-2026-10-02-1227.pdf",
            state=JobState(
                state=9,
                reasons=("job-completed-successfully",),
                impressions=16,
                sheets=0,
            ),
        )
    ]


def test_a_printer_with_nothing_printing_has_no_jobs() -> None:
    assert decode_jobs(PRINTER_IDLE) == []


def test_each_job_group_becomes_its_own_job() -> None:
    """Attributes must not leak from one job into the next: the second job
    here has no impressions, and must not inherit the first one's 60."""
    body = jobs_response(
        integer("job-id", 10210)
        + name_without_language("job-name", "a.pdf")
        + integer("job-state", 7)
        + keyword("job-state-reasons", "job-canceled-at-device")
        + integer("job-impressions-completed", 60),
        integer("job-id", 10211)
        + name_without_language("job-name", "b.pdf")
        + integer("job-state", 5),
    )
    assert decode_jobs(body) == [
        PrinterJob(10210, "a.pdf", JobState(7, ("job-canceled-at-device",), 60, 0)),
        PrinterJob(10211, "b.pdf", JobState(5, (), 0, 0)),
    ]


def test_a_job_without_an_id_or_state_is_an_error() -> None:
    """Without these there is nothing to match on and nothing to judge."""
    with pytest.raises(IppError, match="job-id"):
        decode_jobs(jobs_response(integer("job-state", 9)))
    with pytest.raises(IppError, match="job-state"):
        decode_jobs(jobs_response(integer("job-id", 1)))


def test_a_job_without_a_name_reads_as_unnamed() -> None:
    body = jobs_response(integer("job-id", 1) + integer("job-state", 9))
    assert decode_jobs(body)[0].name == ""


def test_a_refused_jobs_request_is_an_error_with_the_printers_message() -> None:
    body = (
        struct.pack(">HHI", 0x0200, 0x0400, 1)
        + b"\x01"
        + (b"A\x00\x0estatus-message\x00\x0bbad request")
        + b"\x03"
    )
    with pytest.raises(IppError, match="bad request"):
        decode_jobs(body)


def test_the_jobs_request_names_the_printer_and_which_jobs() -> None:
    body = encode_jobs_request("ipp://printer.local:631/ipp/print", "completed")
    assert body[:8] == struct.pack(">HHI", 0x0200, 0x000A, 1)
    assert b"ipp://printer.local:631/ipp/print" in body
    assert b"which-jobs\x00\x09completed" in body
    assert body.count(b"requested-attributes") == 1
    for wanted in (b"job-id", b"job-name", b"job-state", b"job-impressions-completed"):
        assert wanted in body
    assert body.index(b"attributes-charset") < body.index(b"printer-uri")


@pytest.mark.parametrize(
    ("uri", "url"),
    [
        ("ipp://printer.local:631/ipp/print", "http://printer.local:631/ipp/print"),
        ("ipp://printer.local/ipp/print", "http://printer.local:631/ipp/print"),
        ("ipps://printer.local/ipp/print", "https://printer.local:631/ipp/print"),
        ("ipps://printer.local:443/ipp/print", "https://printer.local:443/ipp/print"),
    ],
)
def test_ipp_uris_become_the_http_urls_they_name(uri: str, url: str) -> None:
    """IPP is HTTP on port 631 unless the URI says otherwise (RFC 8010)."""
    assert http_url(uri) == url


def test_a_uri_that_is_not_ipp_has_no_http_url() -> None:
    with pytest.raises(IppError, match="usb://"):
        http_url("usb://Brother/MFC?serial=1")


def test_printer_jobs_asks_for_both_running_and_finished_jobs() -> None:
    """A job CUPS has finished sending may still be printing, so the
    printer's not-completed list matters as much as its completed one."""
    sent: list[tuple[str, bytes]] = []
    replies = iter([PRINTER_IDLE, PRINTER_COMPLETED])

    def poster(url: str, body: bytes) -> bytes:
        sent.append((url, body))
        return next(replies)

    uri = "ipp://printer.local:631/ipp/print"
    jobs = printer_jobs(uri, poster=poster)
    assert [job.id for job in jobs] == [10211]
    assert sent == [
        (
            "http://printer.local:631/ipp/print",
            encode_jobs_request(uri, "not-completed"),
        ),
        ("http://printer.local:631/ipp/print", encode_jobs_request(uri, "completed")),
    ]


def test_post_to_reaches_any_url_and_names_it_on_failure(monkeypatch) -> None:
    import contextlib
    import urllib.request

    from newsprint import cupsjob

    seen: list[urllib.request.Request] = []

    @contextlib.contextmanager
    def fake_urlopen(request, timeout, context=None):
        seen.append(request)
        yield io.BytesIO(PRINTER_IDLE)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert cupsjob.post_to("http://printer.local:631/ipp/print", b"x") == PRINTER_IDLE
    assert seen[0].full_url == "http://printer.local:631/ipp/print"
    assert seen[0].headers["Content-type"] == cupsjob.CONTENT_TYPE

    def refuse(request, timeout, context=None):
        raise OSError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(IppError, match="printer.local"):
        cupsjob.post_to("http://printer.local:631/ipp/print", b"x")


def test_a_printers_self_signed_certificate_is_accepted(monkeypatch) -> None:
    """Printers ship self-signed certificates, so verifying one would make
    ipps:// printers unreachable. The query only reads job counters."""
    import contextlib
    import ssl
    import urllib.request

    from newsprint import cupsjob

    contexts: list[ssl.SSLContext | None] = []

    @contextlib.contextmanager
    def fake_urlopen(request, timeout, context=None):
        contexts.append(context)
        yield io.BytesIO(PRINTER_IDLE)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cupsjob.post_to("https://printer.local:631/ipp/print", b"x")
    cupsjob.post_to("http://printer.local:631/ipp/print", b"x")
    assert contexts[0] is not None
    assert contexts[0].verify_mode == ssl.CERT_NONE
    assert contexts[1] is None


# --- closing mutation-testing gaps (2026-10-02 audit) ---------------------


def test_the_request_id_reaches_the_header() -> None:
    """IPP replies echo the id; a request that drops it cannot be paired
    with its reply."""
    assert encode_request(1, request_id=7)[:8] == struct.pack(">HHI", 0x0200, 0x0009, 7)


def test_the_device_uri_request_names_its_attributes_exactly() -> None:
    """A substring check passes for "XXprinter-uriXX" too; these are the
    exact encoded attributes cupsd looks for."""
    body = encode_device_uri_request("Q")
    assert b"\x45\x00\x0bprinter-uri\x00\x1aipp://localhost/printers/Q" in body
    assert b"\x44\x00\x14requested-attributes\x00\x0adevice-uri\x03" in body


def test_the_jobs_request_names_the_printer_exactly() -> None:
    uri = "ipp://p/ipp/print"
    body = encode_jobs_request(uri, "completed")
    assert (
        b"\x45\x00\x0bprinter-uri" + struct.pack(">H", len(uri)) + uri.encode() in body
    )


def test_the_device_uri_is_found_by_name_not_just_by_type() -> None:
    """CUPS sends other URIs in the same group; only device-uri will do."""
    other = "ipp://localhost:631/printers/Q"
    body = (
        struct.pack(">HHI", 0x0200, 0, 1)
        + b"\x04"
        + b"E\x00\x15printer-uri-supported"
        + struct.pack(">H", len(other))
        + other.encode()
        + b"E\x00\ndevice-uri"
        + struct.pack(">H", len(DEVICE_URI))
        + DEVICE_URI.encode()
        + b"\x03"
    )
    assert decode_device_uri(body) == DEVICE_URI


def test_post_to_posts_with_a_deadline(monkeypatch) -> None:
    """A printer that accepts the connection and never answers must not
    hold the run forever."""
    import contextlib
    import urllib.request

    from newsprint import cupsjob

    calls: list[tuple[urllib.request.Request, object]] = []

    @contextlib.contextmanager
    def fake_urlopen(request, timeout, context=None):
        calls.append((request, timeout))
        yield io.BytesIO(PRINTER_IDLE)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cupsjob.post_to("http://p:631/ipp/print", b"x")
    request, timeout = calls[0]
    assert request.get_method() == "POST"
    assert isinstance(timeout, (int, float)) and timeout > 0
