"""Splitter tests, built around the two ways this has silently failed before."""

import os
from pathlib import Path

import pytest

from newsprint.mbox import (
    MboxIntegrityError,
    find_thunderbird_mbox,
    split_mbox,
)

NORMAL = (
    b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
    b"From: sender@example.com\r\n"
    b"Subject: First\r\n"
    b"\r\n"
    b"Body of the first message.\r\n"
    b"\r\n"
)

SECOND = (
    b"From other@example.com Sat Sep  6 11:00:00 2026\r\n"
    b"From: other@example.com\r\n"
    b"Subject: Second\r\n"
    b"\r\n"
    b"Body of the second message.\r\n"
    b"\r\n"
)

# Thunderbird writes a separator with nothing after "From " but a carriage
# return. A splitter demanding a trailing four-digit year reads a whole folder
# as one message and reports it as safe to delete.
BARE = (
    b"From \r\n"
    b"From: third@example.com\r\n"
    b"Subject: Third\r\n"
    b"\r\n"
    b"Body of the third message.\r\n"
    b"\r\n"
)


def _write(tmp_path: Path, *chunks: bytes) -> Path:
    path = tmp_path / "folder"
    path.write_bytes(b"".join(chunks))
    return path


def test_splits_normal_separators(tmp_path: Path) -> None:
    messages = list(split_mbox(_write(tmp_path, NORMAL, SECOND)))
    assert len(messages) == 2
    assert b"Subject: First" in messages[0]
    assert b"Subject: Second" in messages[1]


def test_splits_a_bare_thunderbird_separator(tmp_path: Path) -> None:
    messages = list(split_mbox(_write(tmp_path, NORMAL, BARE)))
    assert len(messages) == 2
    assert b"Subject: Third" in messages[1]


def test_a_body_line_beginning_with_From_does_not_split(tmp_path: Path) -> None:
    """ "From " mid-body is not a separator: no blank line before it, and the
    next line is not a header."""
    body = (
        b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\n"
        b"Subject: Quoting\r\n"
        b"\r\n"
        b"She wrote:\r\n"
        b"From now on we do it differently.\r\n"
        b"And that was that.\r\n"
        b"\r\n"
    )
    assert len(list(split_mbox(_write(tmp_path, body)))) == 1


def test_every_byte_is_accounted_for(tmp_path: Path) -> None:
    path = _write(tmp_path, NORMAL, SECOND, BARE)
    parsed = sum(len(message) for message in split_mbox(path))
    assert parsed == path.stat().st_size


def test_integrity_error_when_bytes_go_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the buffered-read bug: make the reader return a short file."""
    path = _write(tmp_path, NORMAL, SECOND)
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: NORMAL + SECOND[: len(SECOND) // 2]
    )
    with pytest.raises(MboxIntegrityError, match="bytes"):
        list(split_mbox(path))


def test_empty_file_yields_nothing(tmp_path: Path) -> None:
    assert list(split_mbox(_write(tmp_path, b""))) == []


def test_unix_line_endings(tmp_path: Path) -> None:
    """Unix-style line endings with \\n\\n blank line."""
    unix_msg = (
        b"From sender@example.com Fri Sep  5 10:00:00 2026\n"
        b"From: sender@example.com\n"
        b"Subject: Unix\n"
        b"\n"
        b"Body of the unix message.\n"
        b"\n"
    )
    unix_msg2 = (
        b"From other@example.com Sat Sep  6 11:00:00 2026\n"
        b"From: other@example.com\n"
        b"Subject: Unix2\n"
        b"\n"
        b"Body of the second unix message.\n"
        b"\n"
    )
    messages = list(split_mbox(_write(tmp_path, unix_msg, unix_msg2)))
    assert len(messages) == 2
    assert b"Subject: Unix" in messages[0]
    assert b"Subject: Unix2" in messages[1]


def test_no_separator_with_content_raises_error(tmp_path: Path) -> None:
    """Data exists but no valid separators found - should raise error."""
    bad_mbox = b"This is just content with no From separator\n"
    with pytest.raises(MboxIntegrityError, match="no message separator found"):
        list(split_mbox(_write(tmp_path, bad_mbox)))


def test_from_line_with_no_header_after_does_not_split(tmp_path: Path) -> None:
    """A From line followed by non-header content should not split."""
    content = (
        b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\n"
        b"Subject: First\r\n"
        b"\r\n"
        b"Body text.\r\n"
        b"From this is not a separator because no header follows\r\n"
        b"More body text.\r\n"
        b"\r\n"
    )
    messages = list(split_mbox(_write(tmp_path, content)))
    assert len(messages) == 1
    assert b"Subject: First" in messages[0]


def test_multiple_messages_with_varied_separators(tmp_path: Path) -> None:
    """Test splitting multiple messages with various separator styles."""
    # First message normal, second with bare separator, third normal
    content = (
        b"From first@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: first@example.com\r\n"
        b"Subject: First\r\n"
        b"\r\n"
        b"Body text one.\r\n"
        b"\r\n"
        b"From \r\n"
        b"From: second@example.com\r\n"
        b"Subject: Bare\r\n"
        b"\r\n"
        b"Body text two.\r\n"
        b"\r\n"
        b"From third@example.com Mon Sep  7 12:00:00 2026\r\n"
        b"From: third@example.com\r\n"
        b"Subject: Third\r\n"
        b"\r\n"
        b"Body text three.\r\n"
        b"\r\n"
    )
    messages = list(split_mbox(_write(tmp_path, content)))
    assert len(messages) == 3
    assert b"Subject: First" in messages[0]
    assert b"Subject: Bare" in messages[1]
    assert b"Subject: Third" in messages[2]


def test_preamble_before_first_separator_raises_error(tmp_path: Path) -> None:
    """File with preamble before first separator must raise."""
    preamble = b"This is preamble text\n\n"  # Needs blank line before From
    content = (
        preamble + b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\n"
        b"Subject: Test\r\n"
        b"\r\n"
        b"Body text.\r\n"
        b"\r\n"
    )
    with pytest.raises(MboxIntegrityError, match="preamble"):
        list(split_mbox(_write(tmp_path, content)))


def test_unix_lf_only_mid_body_from_does_not_split(tmp_path: Path) -> None:
    """Unix LF-only: From line in body without blank line before it."""
    body = (
        b"From sender@example.com Fri Sep  5 10:00:00 2026\n"
        b"From: sender@example.com\n"
        b"Subject: Quoting\n"
        b"\n"
        b"She wrote:\n"
        b"From now on we do it differently.\n"
        b"And that was that.\n"
        b"\n"
    )
    messages = list(split_mbox(_write(tmp_path, body)))
    assert len(messages) == 1
    assert b"Subject: Quoting" in messages[0]


def test_unix_lf_only_separators_with_blank_lines(tmp_path: Path) -> None:
    """Unix LF-only: proper separators with blank lines."""
    content = (
        b"From first@example.com Fri Sep  5 10:00:00 2026\n"
        b"From: first@example.com\n"
        b"Subject: First\n"
        b"\n"
        b"Body text one.\n"
        b"\n"
        b"From second@example.com Sat Sep  6 11:00:00 2026\n"
        b"From: second@example.com\n"
        b"Subject: Second\n"
        b"\n"
        b"Body text two.\n"
        b"\n"
    )
    messages = list(split_mbox(_write(tmp_path, content)))
    assert len(messages) == 2
    assert b"Subject: First" in messages[0]
    assert b"Subject: Second" in messages[1]


def test_from_at_eof_without_newline(tmp_path: Path) -> None:
    """From line at end of file with no newline after it."""
    content = (
        b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\n"
        b"Subject: First\r\n"
        b"\r\n"
        b"Body text.\r\n"
        b"\r\nFrom end-of-file"  # No newline after From line
    )
    messages = list(split_mbox(_write(tmp_path, content)))
    # The From without a newline after it won't have a header, so should be 1 message
    assert len(messages) == 1


def test_a_leading_newline_before_the_first_separator_is_rejected(
    tmp_path: Path,
) -> None:
    """A match at position 1 cannot have a blank line before it.

    The regex matches the F in "From" at position 1 (after the leading \n).
    _preceded_by_blank_line is called for position 1 and returns False
    (line 35: position < 2). No valid separator at position 0 means the
    file is rejected as having a preamble.
    """
    path = _write(
        tmp_path,
        b"\nFrom sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\nSubject: T\r\n\r\nBody.\r\n\r\n",
    )
    with pytest.raises(MboxIntegrityError):
        list(split_mbox(path))


def test_a_leading_crlf_before_the_first_separator_is_rejected(
    tmp_path: Path,
) -> None:
    """A match at position 2 with leading CRLF.

    The regex matches the F in "From" at position 2 (after the leading \r\n).
    _preceded_by_blank_line is called for position 2 and returns False
    (line 43: position < 3 after detecting \r\n). No valid separator at
    position 0 means the file is rejected as having a preamble.
    """
    path = _write(
        tmp_path,
        b"\r\nFrom sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\nSubject: T\r\n\r\nBody.\r\n\r\n",
    )
    with pytest.raises(MboxIntegrityError):
        list(split_mbox(path))


def test_a_match_not_preceded_by_a_newline_is_rejected() -> None:
    """Reaches a branch split_mbox cannot: the regex guarantees a preceding
    newline, but the predicate does not assume its caller.

    Direct unit test of _preceded_by_blank_line as a general predicate.
    """
    from newsprint.mbox import _preceded_by_blank_line

    assert _preceded_by_blank_line(b"xFrom ", 1) is False


def test_no_thunderbird_profile_means_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert find_thunderbird_mbox() is None


def test_a_cached_folder_is_found_whatever_the_profile_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile directory and mail host are per-user; neither is hardcoded."""
    target = (
        tmp_path
        / "Library/Thunderbird/Profiles/xyz789.default"
        / "ImapMail/imap.example.com/INBOX.sbd/toprint"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(NORMAL)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert find_thunderbird_mbox() == target


# find_thunderbird_mbox() touches Path.home() and, if a profile exists,
# reads the real mailbox it finds - unacceptable to run just from importing
# this module during collection, on a contributor's own machine, whether or
# not the test below is even selected. Opt in explicitly instead.
_RUN_REAL_MBOX_TEST = os.environ.get("NEWSPRINT_TEST_REAL_MBOX") == "1"


@pytest.mark.skipif(
    not _RUN_REAL_MBOX_TEST,
    reason="set NEWSPRINT_TEST_REAL_MBOX=1 to run against a real local "
    "Thunderbird mbox",
)
def test_real_mbox_parses_completely() -> None:
    """The guard that matters, run against a large real folder.

    Opt-in only, and the lookup happens here at test-run time rather than
    at module import: skipped by default on every machine, including one
    with a local Thunderbird profile.
    """
    real_mbox = find_thunderbird_mbox()
    if real_mbox is None:
        pytest.skip("no local Thunderbird mbox")
    parsed = sum(len(message) for message in split_mbox(real_mbox))
    assert parsed == real_mbox.stat().st_size
