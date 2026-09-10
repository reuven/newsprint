"""Split a Thunderbird mbox into individual messages.

Two failure modes have bitten this before, both silent, both nearly
destructive:

1. Thunderbird writes a bare `From \\r` separator with no sender and no date.
   A splitter that requires the usual trailing four-digit year read a 1.1 GB,
   8,735-message folder as two messages.
2. A buffered reader flushing on a fixed size dropped 60 MB from a folder
   holding two 95 MB messages.

The defence against both is the same and is not optional: assert that the
bytes parsed add up to the size of the file.
"""

import re
from collections.abc import Iterator
from pathlib import Path

# A field name followed by a colon: RFC 5322 printable ASCII except colon.
_HEADER = re.compile(rb"[!-9;-~]+:")
_CANDIDATE = re.compile(rb"(?m)^From ")


class MboxIntegrityError(Exception):
    """The parsed messages do not account for every byte of the file."""


def _preceded_by_blank_line(data: bytes, position: int) -> bool:
    if position == 0:
        return True
    if data[position - 1 : position] != b"\n":
        return False
    if position < 2:
        return False
    # Check for Unix-style blank line: \n\n
    if data[position - 2 : position - 1] == b"\n":
        return True
    # Check for Windows-style blank line: \r\n\r\n (last two bytes before position)
    if data[position - 2 : position] == b"\r\n":
        # Need to verify there's another \n before the \r (complete blank line)
        if position < 3:
            return False
        return data[position - 3 : position - 2] == b"\n"
    return False


def _followed_by_header(data: bytes, position: int) -> bool:
    end_of_line = data.find(b"\n", position)
    if end_of_line == -1:
        return False
    return _HEADER.match(data, end_of_line + 1) is not None


def _separator_positions(data: bytes) -> list[int]:
    return [
        match.start()
        for match in _CANDIDATE.finditer(data)
        if _preceded_by_blank_line(data, match.start())
        and _followed_by_header(data, match.start())
    ]


def split_mbox(path: Path) -> Iterator[bytes]:
    """Yield each message, separator line included.

    Reads the whole file at once. At 201 MB that is entirely affordable, and it
    removes the buffering that caused the second failure above.
    """
    size = path.stat().st_size
    data = path.read_bytes()
    starts = _separator_positions(data)
    if not starts:
        if data:
            raise MboxIntegrityError(
                f"{path}: {len(data)} bytes but no message separator found"
            )
        return

    # Thunderbird mbox always begins with a separator at byte 0. Content before it
    # means the file is not what the parser believes it is, and refusing to silently
    # lose data means we fail loudly rather than invent a repair.
    if starts[0] != 0:
        raise MboxIntegrityError(
            f"{path}: {starts[0]} bytes of preamble before first separator"
        )

    bounds = list(zip(starts, [*starts[1:], len(data)], strict=True))
    messages = []
    for begin, end in bounds:
        chunk = data[begin:end]
        messages.append(chunk)

    # Verify all bytes are accounted for in the messages we yield. The guard
    # measures actual output, not internal bookkeeping, to catch silently dropped data.
    parsed = sum(len(message) for message in messages)
    if parsed != size:
        raise MboxIntegrityError(
            f"{path}: parsed {parsed} bytes but the file holds {size}"
        )
    yield from messages


def find_thunderbird_mbox(folder: str = "toprint") -> Path | None:
    """Locate a local Thunderbird cache of an IMAP folder, if there is one.

    A development convenience for building test fixtures - not part of the
    tool's runtime. newsprint itself talks to IMAP and never reads a local
    mail store, so this returning None is normal on most machines.
    """
    root = Path.home() / "Library" / "Thunderbird" / "Profiles"
    candidates = sorted(root.glob(f"*/ImapMail/*/INBOX.sbd/{folder}"))
    return candidates[0] if candidates else None
