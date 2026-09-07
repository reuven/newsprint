"""Splitter tests, built around the two ways this has silently failed before."""

from pathlib import Path

import pytest

from shabbat_print.mbox import (
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
    """"From " mid-body is not a separator: no blank line before it, and the
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
