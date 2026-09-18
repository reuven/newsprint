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

The server is assumed to be the local cupsd, on the grounds that
`printer.py` already shells out to a local `lp`. A remote CUPS would need
this address to follow it.
"""

import struct
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

SERVER = "http://localhost:631/"
CONTENT_TYPE = "application/ipp"

_VERSION = 0x0200  # IPP/2.0
_GET_JOB_ATTRIBUTES = 0x0009
_HEADER = struct.Struct(">HHI")

# Delimiters open an attribute group; anything above them carries a value.
_END_OF_ATTRIBUTES = 0x03
_LAST_DELIMITER = 0x05

_TAG_INTEGER = 0x21
_TAG_ENUM = 0x23
_TAG_TEXT = 0x41
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

Transport = Callable[[bytes], bytes]


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


def encode_request(job_id: int, request_id: int = 1) -> bytes:
    """Build a Get-Job-Attributes for one job.

    The first two attributes are fixed by RFC 8010 and their order is not
    a style choice: cupsd rejects a request that opens with anything but
    attributes-charset and attributes-natural-language.
    """
    body = _HEADER.pack(_VERSION, _GET_JOB_ATTRIBUTES, request_id)
    body += b"\x01"  # operation-attributes-tag
    body += _attribute(_TAG_CHARSET, "attributes-charset", "utf-8")
    body += _attribute(_TAG_NATURAL_LANGUAGE, "attributes-natural-language", "en")
    body += _attribute(_TAG_URI, "job-uri", f"ipp://localhost:631/jobs/{job_id}")
    body += _attribute(_TAG_KEYWORD, "requested-attributes", _WANTED[0])
    for wanted in _WANTED[1:]:
        body += _additional(_TAG_KEYWORD, wanted)
    return body + bytes([_END_OF_ATTRIBUTES])


def _walk(raw: bytes) -> list[tuple[int, str, bytes]]:
    """Every attribute in the message, as (tag, name, value).

    A nameless attribute inherits the previous name, which is how 1setOf
    values arrive. Anything that runs off the end of the buffer is a
    truncated message rather than something to interpret optimistically.
    """
    attributes: list[tuple[int, str, bytes]] = []
    offset = _HEADER.size
    name = ""
    while offset < len(raw):
        tag = raw[offset]
        offset += 1
        if tag == _END_OF_ATTRIBUTES:
            break
        if tag <= _LAST_DELIMITER:
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
        attributes.append((tag, name, value))
    return attributes


def decode_response(raw: bytes) -> JobState:
    """Read a Get-Job-Attributes reply, or say why it cannot be read.

    An IPP error arrives as a perfectly ordinary HTTP 200, so the status
    code in the header is the only thing that distinguishes "job 8727 is
    finished" from "job 8727 does not exist". Getting that wrong would
    retire the mail on the strength of a error message.
    """
    if len(raw) < _HEADER.size:
        raise IppError("truncated IPP response")
    _, status, _ = _HEADER.unpack_from(raw)
    attributes = _walk(raw)

    if status >= 0x0100:
        message = next(
            (
                value.decode()
                for tag, name, value in attributes
                if name == "status-message" and tag == _TAG_TEXT
            ),
            f"IPP status 0x{status:04x}",
        )
        raise IppError(message)

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


def _post(body: bytes) -> bytes:
    request = urllib.request.Request(
        SERVER, data=body, headers={"Content-Type": CONTENT_TYPE}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            return bytes(reply.read())
    except (urllib.error.URLError, OSError) as error:
        raise IppError(f"could not reach CUPS at {SERVER}: {error}") from error


def fetch(job_id: int, transport: Transport = _post) -> JobState:
    """Current state of one job. `transport` is injected by the tests."""
    return decode_response(transport(encode_request(job_id)))
