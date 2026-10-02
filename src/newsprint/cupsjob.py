"""Ask CUPS what actually happened to a job.

`lp` exiting cleanly means the queue accepted the document, and nothing
more. Worse, a job that stops partway through still ends as `completed`:
on 2026-09-18 a packet put 67 of its 107 sides on paper and CUPS reported
success, so newsprint retired all 36 messages while the printer still had
seven minutes of work ahead of it. `job-state` cannot distinguish those
two outcomes. `job-impressions-completed` can, which is why this module
exists at all.

That attribute is only reachable over IPP. `lpstat` does not report it,
and `ipptool` - which does - is a separate package on most Linux
distributions rather than something newsprint can assume. So this speaks
IPP directly: it is a tagged binary format over an HTTP POST (RFC 8010),
and one request with four requested attributes is little enough of it to
implement here and avoid both a system dependency and a Python one.

CUPS's count is itself only half the story. It counts a side when the
filter has finished *sending* it, so it runs ahead of paper by whatever
the printer holds in memory: on 2026-10-02 the printer ran out of memory
and cancelled a job at 60 of 116 sides while CUPS reported 66. Only the
printer's own job record says what reached paper, so this module also
asks CUPS where a queue's printer is (`device_uri`) and asks that printer
for its jobs (`printer_jobs`).

The server is assumed to be the local cupsd, on the grounds that
`printer.py` already shells out to a local `lp`. A remote CUPS would need
this address to follow it.
"""

import ssl
import struct
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

SERVER = "http://localhost:631/"
CONTENT_TYPE = "application/ipp"

_VERSION = 0x0200  # IPP/2.0
_GET_JOBS = 0x000A
_GET_JOB_ATTRIBUTES = 0x0009
_GET_PRINTER_ATTRIBUTES = 0x000B
_HEADER = struct.Struct(">HHI")

# Delimiters open an attribute group; anything above them carries a value.
_JOB_ATTRIBUTES = 0x02
_END_OF_ATTRIBUTES = 0x03
_LAST_DELIMITER = 0x05

_TAG_INTEGER = 0x21
_TAG_ENUM = 0x23
_TAG_TEXT_WITH_LANGUAGE = 0x35
_TAG_NAME_WITH_LANGUAGE = 0x36
_TAG_TEXT = 0x41
_TAG_NAME = 0x42
_TAG_KEYWORD = 0x44
_TAG_URI = 0x45
_TAG_CHARSET = 0x47
_TAG_NATURAL_LANGUAGE = 0x48
_NUMERIC = frozenset({_TAG_INTEGER, _TAG_ENUM})

# RFC 8011 section 5.3.7. Everything from canceled up is an ending.
_JOB_CANCELED = 7
_JOB_COMPLETED = 9

_WANTED = (
    "job-state",
    "job-state-reasons",
    "job-impressions-completed",
    "job-media-sheets-completed",
)

# The printer's own job list. job-media-sheets-completed is asked for and
# often refused - the Brother this was built against does not report it -
# so callers derive sheets from impressions when they need them.
_WANTED_FROM_PRINTER = ("job-id", "job-name", *_WANTED)

# RFC 8010: IPP is HTTP, on port 631 unless the URI names another.
_IPP_PORT = 631
_HTTP_SCHEME = {"ipp": "http", "ipps": "https"}

Transport = Callable[[bytes], bytes]
Poster = Callable[[str, bytes], bytes]


class IppError(Exception):
    """The job's state could not be established."""


@dataclass(frozen=True, slots=True)
class JobState:
    """What CUPS says about one job, as of one moment.

    `impressions` counts sides, not sheets, and is the only field that
    reveals a short print - compare it against the page count of the
    document that was spooled. `reasons` is worth logging and worth
    ignoring in a decision: macOS CUPS stamps every completed job with
    `processing-to-stop-point`, including two-page ones that printed
    perfectly, so it carries no signal about truncation.
    """

    state: int
    reasons: tuple[str, ...]
    impressions: int
    sheets: int

    @property
    def terminal(self) -> bool:
        """The job has stopped, one way or another. Nothing more will print."""
        return self.state >= _JOB_CANCELED

    @property
    def succeeded(self) -> bool:
        """CUPS ended the job normally - which is not the same as the whole
        document reaching paper. See the module docstring."""
        return self.state == _JOB_COMPLETED


def _attribute(tag: int, name: str, value: str) -> bytes:
    encoded, text = name.encode(), value.encode()
    return (
        struct.pack(">BH", tag, len(encoded))
        + encoded
        + struct.pack(">H", len(text))
        + text
    )


def _additional(tag: int, value: str) -> bytes:
    """A further value for the preceding attribute, which RFC 8010 encodes
    as a nameless one rather than repeating the name."""
    text = value.encode()
    return struct.pack(">BHH", tag, 0, len(text)) + text


def _encode(
    operation: int,
    target: tuple[str, str],
    wanted: tuple[str, ...],
    extra: bytes = b"",
    request_id: int = 1,
) -> bytes:
    """One request: the fixed opening, the object it is about, anything
    operation-specific, and the attributes wanted back.

    The first two attributes are fixed by RFC 8010 and their order is not
    a style choice: cupsd rejects a request that opens with anything but
    attributes-charset and attributes-natural-language.
    """
    name, uri = target
    body = _HEADER.pack(_VERSION, operation, request_id)
    body += b"\x01"  # operation-attributes-tag
    body += _attribute(_TAG_CHARSET, "attributes-charset", "utf-8")
    body += _attribute(_TAG_NATURAL_LANGUAGE, "attributes-natural-language", "en")
    body += _attribute(_TAG_URI, name, uri)
    body += extra
    body += _attribute(_TAG_KEYWORD, "requested-attributes", wanted[0])
    for more in wanted[1:]:
        body += _additional(_TAG_KEYWORD, more)
    return body + bytes([_END_OF_ATTRIBUTES])


def encode_request(job_id: int, request_id: int = 1) -> bytes:
    """Build a Get-Job-Attributes for one CUPS job."""
    return _encode(
        _GET_JOB_ATTRIBUTES,
        ("job-uri", f"ipp://localhost:631/jobs/{job_id}"),
        _WANTED,
        request_id=request_id,
    )


def encode_device_uri_request(queue: str) -> bytes:
    """Ask CUPS where a queue sends its jobs."""
    return _encode(
        _GET_PRINTER_ATTRIBUTES,
        ("printer-uri", f"ipp://localhost/printers/{queue}"),
        ("device-uri",),
    )


def encode_jobs_request(printer_uri: str, which: str) -> bytes:
    """A Get-Jobs to the printer itself. `which` is "completed" or
    "not-completed" - IPP has no single list holding both."""
    return _encode(
        _GET_JOBS,
        ("printer-uri", printer_uri),
        _WANTED_FROM_PRINTER,
        extra=_attribute(_TAG_KEYWORD, "which-jobs", which),
    )


def _walk_groups(raw: bytes) -> list[tuple[int, int, int, str, bytes]]:
    """Every attribute in the message, as (group, group-tag, tag, name, value).

    `group` counts delimiters, so two job groups in a Get-Jobs reply stay
    apart even though they share a tag. A nameless attribute inherits the
    previous name, which is how 1setOf values arrive. Anything that runs
    off the end of the buffer is a truncated message rather than something
    to interpret optimistically.
    """
    attributes: list[tuple[int, int, int, str, bytes]] = []
    offset = _HEADER.size
    name = ""
    group, group_tag = 0, 0
    while offset < len(raw):
        tag = raw[offset]
        offset += 1
        if tag == _END_OF_ATTRIBUTES:
            break
        if tag <= _LAST_DELIMITER:
            group, group_tag = group + 1, tag
            continue
        try:
            (name_length,) = struct.unpack_from(">H", raw, offset)
            offset += 2
            if name_length:
                name = raw[offset : offset + name_length].decode()
                offset += name_length
            (value_length,) = struct.unpack_from(">H", raw, offset)
            offset += 2
        except struct.error as error:
            raise IppError("truncated IPP response") from error
        value = raw[offset : offset + value_length]
        if len(value) != value_length:
            raise IppError("truncated IPP response")
        offset += value_length
        attributes.append((group, group_tag, tag, name, value))
    return attributes


def _checked(raw: bytes) -> list[tuple[int, int, int, str, bytes]]:
    """The message's attributes, or the server's reason it has none.

    An IPP error arrives as a perfectly ordinary HTTP 200, so the status
    code in the header is the only thing that distinguishes "job 8727 is
    finished" from "job 8727 does not exist". Getting that wrong would
    retire the mail on the strength of a error message.
    """
    if len(raw) < _HEADER.size:
        raise IppError("truncated IPP response")
    _, status, _ = _HEADER.unpack_from(raw)
    attributes = _walk_groups(raw)
    if status >= 0x0100:
        message = next(
            (
                value.decode()
                for _, _, tag, name, value in attributes
                if name == "status-message" and tag == _TAG_TEXT
            ),
            f"IPP status 0x{status:04x}",
        )
        raise IppError(message)
    return attributes


def _job_state(attributes: list[tuple[int, str, bytes]]) -> JobState:
    numbers = {
        name: int.from_bytes(value, "big", signed=True)
        for tag, name, value in attributes
        if tag in _NUMERIC
    }
    if "job-state" not in numbers:
        raise IppError("IPP response carried no job-state")
    return JobState(
        state=numbers["job-state"],
        reasons=tuple(
            value.decode()
            for tag, name, value in attributes
            if name == "job-state-reasons" and tag == _TAG_KEYWORD
        ),
        impressions=numbers.get("job-impressions-completed", 0),
        sheets=numbers.get("job-media-sheets-completed", 0),
    )


def decode_response(raw: bytes) -> JobState:
    """Read a Get-Job-Attributes reply, or say why it cannot be read."""
    return _job_state([(t, n, v) for _, _, t, n, v in _checked(raw)])


def decode_device_uri(raw: bytes) -> str:
    """Read the device-uri out of a Get-Printer-Attributes reply."""
    for _, _, tag, name, value in _checked(raw):
        if name == "device-uri" and tag == _TAG_URI:
            return value.decode()
    raise IppError("CUPS reported no device-uri for the queue")


def _name(tag: int, value: bytes) -> str:
    """Text out of a name or text value. The with-language forms pack a
    language and the text into one value, each with its own length."""
    if tag in (_TAG_NAME_WITH_LANGUAGE, _TAG_TEXT_WITH_LANGUAGE):
        (language_length,) = struct.unpack_from(">H", value, 0)
        start = 2 + language_length + 2
        return value[start:].decode()
    return value.decode()


@dataclass(frozen=True, slots=True)
class PrinterJob:
    """One job as the printer itself records it - the only record of how
    many sides actually reached paper."""

    id: int
    name: str
    state: JobState


def decode_jobs(raw: bytes) -> list[PrinterJob]:
    """Read a Get-Jobs reply: one PrinterJob per job-attributes group."""
    groups: dict[int, list[tuple[int, str, bytes]]] = {}
    for group, group_tag, tag, name, value in _checked(raw):
        if group_tag == _JOB_ATTRIBUTES:
            groups.setdefault(group, []).append((tag, name, value))

    jobs = []
    for attributes in groups.values():
        number = next(
            (
                int.from_bytes(value, "big", signed=True)
                for tag, name, value in attributes
                if name == "job-id" and tag == _TAG_INTEGER
            ),
            None,
        )
        if number is None:
            raise IppError("the printer reported a job with no job-id")
        jobs.append(
            PrinterJob(
                id=number,
                name=next(
                    (
                        _name(tag, value)
                        for tag, name, value in attributes
                        if name == "job-name"
                    ),
                    "",
                ),
                state=_job_state(attributes),
            )
        )
    return jobs


def http_url(uri: str) -> str:
    """The HTTP address an ipp:// or ipps:// URI stands for."""
    parts = urllib.parse.urlsplit(uri)
    scheme = _HTTP_SCHEME.get(parts.scheme)
    if scheme is None:
        raise IppError(f"{uri} is not an IPP address")
    port = parts.port or _IPP_PORT
    return f"{scheme}://{parts.hostname}:{port}{parts.path}"


def post_to(url: str, body: bytes, place: str = "") -> bytes:
    """POST one IPP request to `url`; `place` names it in an error.

    Printers ship self-signed certificates, so an https:// printer is
    reached without verifying one. What travels is a request for job
    counters and a reply holding them; nothing is sent that a spoofed
    printer could misuse, and the worst a forged reply can do is keep the
    mail.
    """
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": CONTENT_TYPE}, method="POST"
    )
    context = None
    if url.startswith("https:"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as reply:
            return bytes(reply.read())
    except (urllib.error.URLError, OSError) as error:
        raise IppError(f"could not reach {place or url}: {error}") from error


def _post(body: bytes) -> bytes:
    return post_to(SERVER, body, place=f"CUPS at {SERVER}")


def fetch(job_id: int, transport: Transport = _post) -> JobState:
    """Current state of one job. `transport` is injected by the tests."""
    return decode_response(transport(encode_request(job_id)))


def device_uri(queue: str, poster: Poster = post_to) -> str:
    """Where CUPS sends `queue`'s jobs, e.g. dnssd://..., ipp://..., usb://..."""
    return decode_device_uri(
        poster(f"{SERVER}printers/{queue}", encode_device_uri_request(queue))
    )


def printer_jobs(printer_uri: str, poster: Poster = post_to) -> list[PrinterJob]:
    """Every job the printer still remembers, running ones first.

    A job CUPS has finished sending may still be printing, so both lists
    are needed. Printers forget completed jobs quickly - the Brother keeps
    only the latest one - so ask as soon as CUPS lets go.
    """
    url = http_url(printer_uri)
    return [
        job
        for which in ("not-completed", "completed")
        for job in decode_jobs(poster(url, encode_jobs_request(printer_uri, which)))
    ]
