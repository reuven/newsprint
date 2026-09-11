import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar, Self

import pytest
from click.testing import CliRunner

from newsprint.cli import fetch_queue, main, retire_printed
from newsprint.config import load_config
from newsprint.mail import RetireResult
from newsprint.picker import DISPLAY_LIMIT

SAMPLE_CONFIG = """
[mail]
host = "imap.example.com"
user = "someone@example.com"
folder = "INBOX/toprint"
"""

# Long enough to clear the default packet.min_words threshold (250) after
# cleaning, so a fixture built from it is a genuine "Built", not skipped
# as a teaser - H2 added that check to build_one() and this project's own
# live queue default is well above the length of a single sentence.
LONG_PROSE = (
    "The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes. "
) * 15


def _message(headers: str, body: str) -> bytes:
    return (headers.strip() + "\n\n" + body).encode()


RAW_MESSAGE = _message(
    """
From: Newsletter <news@example.com>
Subject: This Week
Date: Fri, 5 Sep 2026 14:22:55 +0000
Message-ID: <issue@example.com>
Content-Type: text/html; charset="utf-8"
""",
    "<html><body><p>Body text.</p></body></html>",
)


class _FakeBox:
    """Stands in for newsprint.mail.Mailbox in unit tests for
    fetch_queue and retire_printed, so those functions can be exercised
    without ever opening a real IMAP connection."""

    instances: ClassVar[list["_FakeBox"]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.retire_calls: list[tuple] = []
        self.retire_result = RetireResult(retired=(), failed=())
        # Mirrors Mailbox.message_count, set by a real SELECT response -
        # 2226 matches FakeIMAP's own default in test_mail.py.
        self.message_count = 2226
        # Per-uid raw message override for fetch_many(); any uid not
        # named here falls back to RAW_MESSAGE. Subclasses below (e.g.
        # _QueueBox) populate this so the real, unmocked fetch_queue()
        # can be driven end to end over one fake connection.
        self.messages: dict[int, bytes] = {}
        self.fetch_many_calls: list[tuple[tuple[int, ...], str]] = []
        # RFC822.SIZE per uid, for fetch_sizes() - the picker's length
        # indicator. A uid with no entry defaults to a "medium" bucket
        # (see picker.length_label's thresholds) so a test that never
        # sets this explicitly still gets a sane, non-crashing label.
        self.sizes: dict[int, int] = {}
        self.fetch_sizes_calls: list[tuple[int, ...]] = []
        _FakeBox.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def search_flagged(self) -> list[int]:
        return [1, 2]

    def search_unflagged_since(self, since) -> list[int]:
        self.since_arg = since
        return []

    def fetch_many(self, uids, items: str = "(UID RFC822)") -> dict[int, bytes]:
        self.fetch_many_calls.append((tuple(uids), items))
        return {uid: self.messages.get(uid, RAW_MESSAGE) for uid in uids}

    def fetch_sizes(self, uids) -> dict[int, int]:
        self.fetch_sizes_calls.append(tuple(uids))
        return {uid: self.sizes.get(uid, 50_000) for uid in uids}

    def trash_folder(self) -> str:
        return "INBOX/Trash"

    def retire(self, uids, trash_folder):
        self.retire_calls.append((tuple(uids), trash_folder))
        return self.retire_result


_STARRED_RAW = _message(
    """
From: Test Weekly <weekly@example.com>
Subject: An Issue
Date: Fri, 5 Sep 2026 12:00:00 +0000
Message-ID: <d@example.com>
Content-Type: text/html; charset="utf-8"
""",
    f"<div><p>{LONG_PROSE}</p></div>",
)


def _candidate_raw(
    uid: int, publication: str, title: str = "Extra Issue", buildable: bool = False
) -> bytes:
    """Raw RFC822 bytes for a message the picker might offer from the
    unstarred review window - the byte-level equivalent of the old
    _candidate() Document helper, needed now that the picker tests below
    drive the real fetch_queue()/fetch_unstarred()/fetch_picked() through
    a fake Mailbox rather than mocking those functions away (they must
    actually fetch headers for the listing and full content for whatever
    gets picked - see cli.py's own module docstring on why). buildable
    controls whether the body clears packet.min_words (H2) once picked."""
    body = f"<div><p>{LONG_PROSE}</p></div>" if buildable else "<p>short</p>"
    return _message(
        f"""
From: {publication} <pub{uid}@example.com>
Subject: {title}
Date: Fri, 4 Sep 2026 12:00:00 +0000
Message-ID: <{uid}@example.com>
Content-Type: text/html; charset="utf-8"
""",
        body,
    )


class _QueueBox(_FakeBox):
    """A starred queue of exactly one message - uid 4, publication "Test
    Weekly", title "An Issue", body long enough to build (mirrors the old
    _queued() Document exactly) - plus whatever a subclass offers as
    unstarred candidates via `unstarred_uids`/`extra_messages`. Used by
    the picker tests, which need the real fetch_queue() to run end to end
    (starred fetch, unstarred scan, prompt, picked fetch) over one fake
    connection, rather than mocking fetch_queue itself away."""

    unstarred_uids: ClassVar[tuple[int, ...]] = ()
    extra_messages: ClassVar[dict[int, bytes]] = {}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.messages = {4: _STARRED_RAW, **self.extra_messages}

    def search_flagged(self) -> list[int]:
        return [4]

    def search_unflagged_since(self, since) -> list[int]:
        self.since_arg = since
        return list(self.unstarred_uids)


@pytest.fixture
def mail_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE_CONFIG)
    return load_config(path)


def test_help_names_the_paper_switch() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--paper" in result.output


def test_the_config_path_is_reported_before_anything_else(
    monkeypatch, tmp_path: Path
) -> None:
    """The user's original complaint was 10-20s of total silence before
    anything appears. The config path - known before any network call -
    must be the very first thing printed, so a run gives feedback
    immediately."""
    monkeypatch.setattr("newsprint.cli.fetch_queue", lambda config, no_pick: ([], None))
    config_path = tmp_path / "absent.toml"

    result = CliRunner().invoke(main, ["--config", str(config_path)])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines[0] == f"Reading config: {config_path}"


def test_an_empty_queue_says_so(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("newsprint.cli.fetch_queue", lambda config, no_pick: ([], None))
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "absent.toml")])
    assert result.exit_code == 0
    assert "Nothing starred" in result.output


def test_dry_run_never_prints_and_never_retires(monkeypatch, tmp_path: Path) -> None:
    """--dry-run must build a real PDF and then stop, leaving mail untouched.

    The queue must be non-empty, or the command returns early and the test
    passes without ever reaching the --dry-run branch.
    """
    from datetime import datetime

    from newsprint.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<a@example.com>", uid=1),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=f"<div><p>{LONG_PROSE}</p></div>",
    )

    spooled: list[Path] = []
    retired: list[list[int]] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: spooled.append(pdf))
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "1 cells" in result.output or "cells" in result.output
    assert spooled == []
    assert retired == []


def test_declining_the_prompt_prints_nothing(monkeypatch, tmp_path: Path) -> None:
    """Answering no at the confirmation must leave mail untouched."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<b@example.com>", uid=2),
        publication="Test Weekly",
        title="Another Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=f"<div><p>{LONG_PROSE}</p></div>",
    )
    spooled: list[Path] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: spooled.append(pdf))
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "x")

    result = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(tmp_path / "absent.toml")],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Mail untouched" in result.output
    assert spooled == []


def _queued(identifier: str = "<d@example.com>", uid: int = 4):
    from datetime import datetime

    from newsprint.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=uid),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=f"<div><p>{LONG_PROSE}</p></div>",
    )


def test_accepting_prints_then_retires_in_that_order(
    monkeypatch, tmp_path: Path
) -> None:
    """The invariant: mail is modified only after a job reaches the queue."""
    events: list[str] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.spool",
        lambda pdf, config: (events.append("spool"), "Printer-1")[1],
    )

    def fake_retire_printed(config, uids, trash):
        events.append(f"retire:{uids}")
        return RetireResult(retired=tuple(uids), failed=())

    monkeypatch.setattr("newsprint.cli.retire_printed", fake_retire_printed)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    assert events == ["spool", "retire:[4]"]


def test_no_retire_spools_but_never_calls_retire_printed(
    monkeypatch, tmp_path: Path
) -> None:
    """--no-retire must print for real, unlike --dry-run, but never touch
    mail: the messages stay starred so they are reprinted next run."""
    spooled: list[Path] = []
    retired: list[list[int]] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.spool",
        lambda pdf, config: (spooled.append(pdf), "Printer-1")[1],
    )
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main,
        ["--no-retire", "--no-preview", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert result.exit_code == 0
    assert len(spooled) == 1  # unlike --dry-run, a real PDF was spooled
    assert retired == []
    assert "starred" in result.output
    assert "next run" in result.output


def test_no_retire_records_the_outcome_as_printed_kept(
    monkeypatch, tmp_path: Path
) -> None:
    """The run log must not call this a completed 'printed' run, or a later
    last_successful_run() would treat a rehearsal as the real thing."""
    recorded: list[dict] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: pytest.fail("retire_printed must not be called"),
    )

    def fake_record(entry, **kw):
        recorded.append(entry)
        return tmp_path / "r"

    monkeypatch.setattr("newsprint.runlog.record", fake_record)

    result = CliRunner().invoke(
        main,
        ["--no-retire", "--no-preview", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert result.exit_code == 0
    outcomes = [entry["outcome"] for entry in recorded]
    assert "printed-kept" in outcomes
    assert "printed" not in outcomes


def test_dry_run_wins_when_combined_with_no_retire(monkeypatch, tmp_path: Path) -> None:
    """--dry-run and --no-retire together are not an error; --dry-run is the
    stricter of the two and wins, so nothing is printed at all."""
    spooled: list[Path] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: spooled.append(pdf))

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-retire",
            "--no-preview",
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert spooled == []


def test_paper_letter_propagates_end_to_end(monkeypatch, tmp_path: Path) -> None:
    """--paper exists solely to propagate one setting through render, trim,
    and impose without drift; nothing previously drove it end to end."""
    import pymupdf

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        [
            "--paper",
            "letter",
            "--dry-run",
            "--no-preview",
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0
    pdf_path = next(
        line.strip()
        for line in result.output.splitlines()
        if line.strip().endswith(".pdf") and "newsprint-" in line
    )
    with pymupdf.open(pdf_path) as document:
        rect = document[0].rect
    assert rect.width == pytest.approx(612.0, abs=1.0)
    assert rect.height == pytest.approx(792.0, abs=1.0)


def test_help_explains_that_no_retire_leaves_mail_starred() -> None:
    """The surprise a user would otherwise hit must be spelled out up front."""
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--no-retire" in result.output
    assert "starred" in result.output


def test_a_url_sourced_document_prints_without_ever_calling_retire(
    monkeypatch, tmp_path: Path
) -> None:
    """A document with no uid (a URL origin, not email) leaves `uids`
    empty even when `trash` is set, so retirement must be skipped
    entirely - the run simply ends after printing, without ever calling
    retire_printed."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    document = Document(
        origin=Origin(kind="url", identifier="https://example.com/article"),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=f"<div><p>{LONG_PROSE}</p></div>",
    )
    retired: list[list[int]] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    assert "Spooled as Printer-1" in result.output
    assert retired == []


def test_a_print_failure_leaves_mail_untouched(monkeypatch, tmp_path: Path) -> None:
    from newsprint.printer import PrintError

    retired: list[list[int]] = []

    def explode(pdf, config):
        raise PrintError("lp: no such printer")

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", explode)
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert "no such printer" in result.output
    assert retired == []


def test_a_bad_config_file_is_reported_cleanly_not_as_a_traceback(
    tmp_path: Path,
) -> None:
    """load_config() used to sit outside cli.py's (MailError, ConfigError)
    handler, so a bad value anywhere in config.toml surfaced as a bare,
    unhandled exception instead of the same clean error message every
    other config or mail problem gets."""
    path = tmp_path / "config.toml"
    path.write_text('[print]\npaper = "foolscap"\n')

    result = CliRunner().invoke(main, ["--config", str(path)])
    assert result.exit_code != 0
    assert not isinstance(result.exception, ValueError)
    assert "unknown paper" in result.output


def test_a_retire_failure_after_printing_is_reported_not_crashed(
    monkeypatch, tmp_path: Path
) -> None:
    """retire_printed() can raise - password_for() or Mailbox.__enter__()
    failing, or the folder reporting READ-ONLY - after the job has
    already reached the print queue. The user must get a clear error
    naming what happened, not a raw traceback leaving them unsure whether
    their mail was touched."""
    from newsprint.mail import MailError

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")

    def exploding_retire(config, uids, trash):
        raise MailError("could not reopen the folder writable")

    monkeypatch.setattr("newsprint.cli.retire_printed", exploding_retire)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert not isinstance(result.exception, MailError)
    assert "Spooled as Printer-1" in result.output
    assert "could not reopen the folder writable" in result.output


def test_an_imap_readonly_error_from_retire_is_reported_not_crashed(
    monkeypatch, tmp_path: Path
) -> None:
    """The other documented failure mode: imaplib itself raises
    IMAP4.readonly when the server answers a writable SELECT with
    READ-ONLY - a plain exception, not a MailError."""
    import imaplib

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")

    def exploding_retire(config, uids, trash):
        raise imaplib.IMAP4.readonly("INBOX/toprint is not writable")

    monkeypatch.setattr("newsprint.cli.retire_printed", exploding_retire)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert not isinstance(result.exception, imaplib.IMAP4.error)
    assert "not writable" in result.output


def test_a_retire_connection_failure_after_printing_is_reported_not_crashed(
    monkeypatch, tmp_path: Path
) -> None:
    """The second connection - a fresh imaplib.IMAP4_SSL(host), opened
    minutes after the first, immediately after the printer has already
    accepted the job - can fail with a raw OSError (socket.gaierror on a
    DNS blip, ssl.SSLError on a dropped VPN) that neither MailError nor
    imaplib.IMAP4.error covers. The run must report that printing
    succeeded and mail was not retired, not let the OSError escape as a
    bare traceback leaving the user unsure what happened."""

    class _ExplodingMailbox:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            raise OSError("[Errno 8] nodename nor servname provided, or not known")

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr("newsprint.cli.Mailbox", _ExplodingMailbox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert not isinstance(result.exception, OSError)
    assert "Spooled as Printer-1" in result.output
    assert "Printed, but could not retire" in result.output


def test_an_unconfigured_account_says_what_to_set(monkeypatch, tmp_path: Path) -> None:
    """With no config file at all, the error names the missing keys."""
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "absent.toml")])
    assert result.exit_code != 0
    assert "mail.host and mail.user" in result.output


def test_retirement_outcome_is_logged(monkeypatch, tmp_path: Path) -> None:
    """runlog records intent before mutating but never recorded the
    RetireResult afterward - the spec's error table requires logging
    which UIDs succeeded, so a partial failure can be recovered by hand
    from the log alone."""
    recorded: list[dict] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: RetireResult(retired=(4,), failed=()),
    )

    def fake_record(entry, **kw):
        recorded.append(entry)
        return tmp_path / "r"

    monkeypatch.setattr("newsprint.runlog.record", fake_record)

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    retired_entries = [entry for entry in recorded if entry["outcome"] == "retired"]
    assert len(retired_entries) == 1
    assert retired_entries[0]["retired"] == [4]
    assert retired_entries[0]["failed"] == []


def test_a_partial_retirement_outcome_is_logged(monkeypatch, tmp_path: Path) -> None:
    """The failed UIDs must be in the log too, not just the succeeded ones."""
    recorded: list[dict] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: (
            [
                _queued(identifier="<d@example.com>", uid=4),
                _queued("<g@example.com>", 7),
            ],
            "INBOX/Trash",
        ),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: RetireResult(retired=(4,), failed=(7,)),
    )

    def fake_record(entry, **kw):
        recorded.append(entry)
        return tmp_path / "r"

    monkeypatch.setattr("newsprint.runlog.record", fake_record)

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    retired_entries = [entry for entry in recorded if entry["outcome"] == "retired"]
    assert len(retired_entries) == 1
    assert retired_entries[0]["retired"] == [4]
    assert retired_entries[0]["failed"] == [7]


def test_an_unrecoverable_message_is_named_in_the_run_log(
    monkeypatch, tmp_path: Path
) -> None:
    """The run log is the recovery mechanism: a message whose rescue
    re-star also failed - the one state the user cannot discover from
    Thunderbird alone - must be named in the log, not only echoed to the
    terminal where it can scroll away."""
    recorded: list[dict] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: RetireResult(
            retired=(), failed=(4,), unrecoverable=(4,)
        ),
    )

    def fake_record(entry, **kw):
        recorded.append(entry)
        return tmp_path / "r"

    monkeypatch.setattr("newsprint.runlog.record", fake_record)

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    retired_entries = [entry for entry in recorded if entry["outcome"] == "retired"]
    assert len(retired_entries) == 1
    assert retired_entries[0]["unrecoverable"] == [4]


def test_image_counts_are_reported(monkeypatch, tmp_path: Path) -> None:
    """The spec requires 'kept N images, dropped M' precisely because a
    silent drop is otherwise indistinguishable from an image that was
    never there. The data was already on Built.document; nothing echoed
    it."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<h@example.com>", uid=11),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=(
            f"<div><p>{LONG_PROSE}</p>"
            '<img src="https://example.com/pixel.gif" width="1" height="1">'
            "</div>"
        ),
    )
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "kept 0 images, dropped 1" in result.output


def test_kept_images_are_counted_and_a_fetch_failure_is_reported_distinctly(
    monkeypatch, tmp_path: Path
) -> None:
    """A kept image (a colon lead-in, here) whose fetch fails must be
    reported as a fetch failure, not folded into the ordinary "dropped"
    count - clean.py already made the editorial call to keep it; the
    image itself just did not come back usable. The `data:` URI below
    resolves entirely locally (no socket, no DNS) so this exercises the
    real render() end to end - not a fake render_fn - while still making
    no network request at all: it is syntactically a valid data URI but
    decodes to bytes Pillow cannot open as an image, the same failure
    shape a dead or malformed CDN response would produce."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<i@example.com>", uid=12),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=(
            f"<div><p>{LONG_PROSE} Here's the chart:</p>"
            '<img src="data:image/png;base64,AAAA" width="600">'
            "</div>"
        ),
    )
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "kept 1 images, dropped 0, 1 fetch failed" in result.output
    assert "image fetch failed: data:image/png;base64,AAAA" in result.output


def test_a_dropped_chrome_block_is_reported(monkeypatch, tmp_path: Path) -> None:
    """A wrong removal must be visible in the run's own output, not just
    recorded on the Document and never shown to anyone."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<f@example.com>", uid=9),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=(f"<div><p>{LONG_PROSE}</p></div><div><p>Unsubscribe</p></div>"),
    )
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "removed block" in result.output
    assert "unsubscribe" in result.output.lower()


def test_a_dropped_duplicate_title_is_reported_distinctly(
    monkeypatch, tmp_path: Path
) -> None:
    """Part E of the derive-chrome spec (2026-09-07): a duplicate-title
    removal is not a wrong removal - the text reappears in the printout
    as render.py's own synthesised headline - so reporting it as a plain
    "removed block" reads as data loss when it is not. That is exactly
    what alarmed the user for real: 'removed block: "This isn't just
    about Jaguar Land Rover (or VW)"' was Ed Conway's own subject line,
    removed as part of clean.py's duplicate-title block and printed
    anyway as the page's own headline."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    html = (
        "<div><h2>An Issue</h2><p>A subtitle</p><p>By The Author</p>"
        "<p>Sep 7</p>"
        f"<p>{LONG_PROSE}</p></div>"
    )
    document = Document(
        origin=Origin(kind="email", identifier="<t@example.com>", uid=10),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
    )
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([document], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "removed duplicate title" in result.output
    assert "an issue" in result.output.lower()
    # Never reported the alarming way - a plain "removed block".
    assert "removed block: 'An Issue'" not in result.output


def test_a_document_that_cannot_be_built_is_reported(
    monkeypatch, tmp_path: Path
) -> None:
    """A newsletter that cleans down to nothing is named, not silently lost."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue", lambda config, no_pick: ([_empty()], None)
    )
    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code == 0
    assert "SKIPPED" in result.output
    assert "Empty Weekly" in result.output
    assert "Nothing could be built" in result.output


def _empty():
    from datetime import datetime

    from newsprint.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier="<e@example.com>", uid=5),
        publication="Empty Weekly",
        title="Nothing",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<div><p>Unsubscribe</p></div>",
    )


def _teaser(uid: int = 6, identifier: str = "<teaser@example.com>"):
    """A headline-and-link teaser: survives clean.py's chrome removal (it
    is real editorial text, not boilerplate) but falls well under the
    default packet.min_words threshold."""
    from datetime import datetime

    from newsprint.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=uid),
        publication="The Times",
        title="Pete Hegseth Is a Wrecking Ball",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<h1>Pete Hegseth Is a Wrecking Ball</h1><p>Read more online.</p>",
    )


def test_a_teaser_is_reported_on_stdout_with_subject_and_word_count(
    monkeypatch, tmp_path: Path
) -> None:
    """H2: skipping must be visible, never silent - the report must name
    the publication, the subject, and the word count that fell short, and
    it must be on stdout (Click's non-error stream), not buried on
    stderr the way an ordinary build failure is."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue", lambda config, no_pick: ([_teaser()], None)
    )
    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code == 0
    assert "SKIPPED" in result.output
    assert "The Times" in result.output
    assert "Pete Hegseth Is a Wrecking Ball" in result.output
    assert "3 words" in result.output  # "Read more online."
    # Explicitly on stdout, unlike the generic SKIPPED-on-build-failure line.
    assert "SKIPPED" in result.stdout


def test_a_skipped_teaser_is_not_retired(monkeypatch, tmp_path: Path) -> None:
    """The retirement invariant: a message that did not reach the PDF must
    stay starred. `uids` is derived only from `built`, so a lone teaser
    with nothing else in the queue must never call retire_printed at all."""
    retired: list[list[int]] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_teaser()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code == 0
    assert "Nothing could be built" in result.output
    assert retired == []


def test_a_skipped_teaser_is_excluded_from_uids_alongside_a_real_build(
    monkeypatch, tmp_path: Path
) -> None:
    """A teaser mixed in with a real newsletter: the real one prints and
    retires, the teaser stays starred - `uids` must name only the one
    that actually reached the PDF."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued(), _teaser()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")

    def fake_retire_printed(config, uids, trash):
        assert uids == [4]  # _queued()'s uid only - the teaser's is absent
        return RetireResult(retired=tuple(uids), failed=())

    monkeypatch.setattr("newsprint.cli.retire_printed", fake_retire_printed)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    assert "SKIPPED" in result.output


def test_skipped_teasers_are_recorded_in_the_run_log(
    monkeypatch, tmp_path: Path
) -> None:
    """A user auditing the log later must be able to see what was left
    out, not just what printed."""
    recorded: list[dict] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued(), _teaser()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: RetireResult(retired=tuple(uids), failed=()),
    )

    def fake_record(entry, **kw):
        recorded.append(entry)
        return tmp_path / "r"

    monkeypatch.setattr("newsprint.runlog.record", fake_record)

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    printed_entries = [entry for entry in recorded if entry["outcome"] == "printed"]
    assert len(printed_entries) == 1
    skipped = printed_entries[0]["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["publication"] == "The Times"
    assert skipped[0]["title"] == "Pete Hegseth Is a Wrecking Ball"
    assert skipped[0]["words"] == 3


def test_fetch_queue_extracts_the_flagged_messages(monkeypatch, mail_config) -> None:
    """Unit-tests fetch_queue's own body, which the CLI-level tests above
    always monkeypatch away: it must open the mailbox read-only, extract a
    Document per flagged uid, and discover the Trash folder.

    no_pick=True keeps this test focused on the starred fetch alone - the
    picker path is exercised separately below."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    asked: list[tuple[str, str]] = []

    def recording_password_for(host: str, user: str) -> str:
        asked.append((host, user))
        return "secret"

    monkeypatch.setattr("newsprint.cli.password_for", recording_password_for)

    documents, trash = fetch_queue(mail_config, no_pick=True)

    assert len(documents) == 2
    assert all(document.title == "This Week" for document in documents)
    assert trash == "INBOX/Trash"
    kwargs = _FakeBox.instances[0].kwargs
    assert kwargs["host"] == "imap.example.com"
    assert kwargs["user"] == mail_config.mail.user
    assert kwargs["folder"] == mail_config.mail.folder
    assert kwargs["password"] == "secret"
    assert asked == [(mail_config.mail.host, mail_config.mail.user)]
    # The whole message, not just its headers: this is the fetch whose
    # results get printed, and a header-only one would render every
    # newsletter as an empty cell.
    assert _FakeBox.instances[0].fetch_many_calls[0][1] == "(UID RFC822)"
    # Exactly one connection - the whole point of sharing it between the
    # starred fetch and the unstarred scan.
    assert len(_FakeBox.instances) == 1


def test_fetch_queue_reuses_one_connection_for_starred_and_unstarred(
    monkeypatch, mail_config
) -> None:
    """The tool used to connect twice: once for the starred queue, once
    more for the unstarred scan. fetch_queue must now open exactly one
    Mailbox for a run that touches both, not two."""

    class _BothBox(_FakeBox):
        def search_unflagged_since(self, since) -> list[int]:
            self.since_arg = since
            self.scanned = True
            return [5, 6]

    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _BothBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    # Force the non-interactive branch deterministically, regardless of
    # whether this test process happens to have a real tty on stdin.
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: False)

    # No no_pick argument: the default is to offer the picker, and this
    # is where that default is exercised. Passing it explicitly here, as
    # every other test does, would leave it free to be flipped.
    documents, trash = fetch_queue(mail_config)

    assert len(_FakeBox.instances) == 1
    assert len(documents) == 2  # the starred pair only - nothing was picked
    assert trash == "INBOX/Trash"
    # And the scan actually happened: "nothing was picked" reads the same
    # whether the picker found nothing or was never offered at all.
    assert getattr(_FakeBox.instances[0], "scanned", False)


def test_fetch_queue_skips_trash_lookup_when_nothing_is_flagged(
    monkeypatch, mail_config
) -> None:
    """No point discovering the Trash folder for a run with nothing to retire."""

    class _EmptyBox(_FakeBox):
        def search_flagged(self) -> list[int]:
            return []

        def trash_folder(self) -> str:
            raise AssertionError("trash_folder must not be called for an empty queue")

    monkeypatch.setattr("newsprint.cli.Mailbox", _EmptyBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    documents, trash = fetch_queue(mail_config, no_pick=True)
    assert documents == []
    assert trash is None


def test_fetch_queue_honors_a_configured_literal_trash_folder(
    monkeypatch, tmp_path: Path
) -> None:
    """config.mail.trash was declared, advertised in config.example.toml,
    and read nowhere: a server with no SPECIAL-USE support had no escape
    hatch and aborted the run. A non-"auto" value must be honored as a
    literal folder name instead of ever calling trash_folder()."""

    class _NoTrashLookupBox(_FakeBox):
        def trash_folder(self) -> str:
            raise AssertionError(
                "trash_folder() must not be called when mail.trash is set"
            )

    path = tmp_path / "config.toml"
    path.write_text(SAMPLE_CONFIG.rstrip() + '\ntrash = "Configured-Trash"\n')
    config = load_config(path)

    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _NoTrashLookupBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    _documents, trash = fetch_queue(config, no_pick=True)
    assert trash == "Configured-Trash"


def test_fetch_queue_still_discovers_trash_when_configured_as_auto(
    monkeypatch, mail_config
) -> None:
    """The default, "auto", must keep discovering via SPECIAL-USE."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    assert mail_config.mail.trash == "auto"
    _documents, trash = fetch_queue(mail_config, no_pick=True)
    assert trash == "INBOX/Trash"


def test_fetch_queue_reports_progress_at_every_step(
    monkeypatch, mail_config, capsys
) -> None:
    """The user's original complaint: 10-20s with no output at all before
    anything appears. Every phase fetch_queue goes through - connecting,
    the folder and its message count, the starred search, and the Trash
    folder - must be visible, not silent."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    fetch_queue(mail_config, no_pick=True)

    output = capsys.readouterr().out
    assert "Connecting to imap.example.com as someone@example.com" in output
    assert "Opened INBOX/toprint (2226 messages)" in output
    assert "2 starred message(s) found" in output
    assert "Discovered Trash folder: INBOX/Trash" in output


def test_fetch_queue_uses_the_configured_trash_folder_without_discovering(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """A literal mail.trash must be reported as used, not discovered -
    trash_folder() is never even called in this branch."""

    class _NoTrashLookupBox(_FakeBox):
        def trash_folder(self) -> str:
            raise AssertionError("must not be called when mail.trash is configured")

    path = tmp_path / "config.toml"
    path.write_text(SAMPLE_CONFIG.rstrip() + '\ntrash = "Configured-Trash"\n')
    config = load_config(path)

    monkeypatch.setattr("newsprint.cli.Mailbox", _NoTrashLookupBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    fetch_queue(config, no_pick=True)

    output = capsys.readouterr().out
    assert "Using configured Trash folder: Configured-Trash" in output


def test_fetch_queue_progress_bar_leaves_no_artifacts_when_not_a_tty(
    monkeypatch, mail_config, capsys
) -> None:
    """click.progressbar over a chunked fetch must hide itself the same
    way the build bar already does, so it never litters captured (non-tty)
    output with carriage returns or fill characters. Below
    Mailbox.FETCH_CHUNK_SIZE uids - every ordinary run - there is no bar
    at all (see cli._fetch_documents), so this asserts the more general
    property: whatever fetch_queue prints, it never contains bar
    artifacts."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    fetch_queue(mail_config, no_pick=True)

    output = capsys.readouterr().out
    assert "\r" not in output
    assert "[" not in output
    assert "#" not in output


def test_fetch_queue_chunks_a_starred_queue_larger_than_fetch_chunk_size(
    monkeypatch, mail_config, capsys
) -> None:
    """Above Mailbox.FETCH_CHUNK_SIZE uids, _fetch_documents chunks the
    fetch across several box.fetch_many() calls, tracked with a
    click.progressbar - which must still hide its own rendering on a
    non-tty, the same as the single-call path already does."""
    from newsprint.mail import FETCH_CHUNK_SIZE

    uids = list(range(1, FETCH_CHUNK_SIZE + 52))  # two chunks: 200 + 51

    class _BigBox(_FakeBox):
        def search_flagged(self) -> list[int]:
            return uids

    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _BigBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    documents, _trash = fetch_queue(mail_config, no_pick=True)

    assert len(documents) == len(uids)
    box = _FakeBox.instances[0]
    assert len(box.fetch_many_calls) == 2
    assert len(box.fetch_many_calls[0][0]) == FETCH_CHUNK_SIZE
    assert len(box.fetch_many_calls[1][0]) == 51

    output = capsys.readouterr().out
    assert "\r" not in output
    assert "[" not in output
    assert "#" not in output


def test_fetch_unstarred_extracts_the_review_window() -> None:
    """fetch_unstarred works off an already-open box (the connection it
    now shares with the starred fetch, via fetch_queue) - it must search
    UNFLAGGED SINCE the given date and extract a partial Document (for
    the listing) per uid found."""
    from datetime import date

    from newsprint.cli import fetch_unstarred
    from newsprint.config import PublicationNames

    class _UnstarredBox(_FakeBox):
        def search_unflagged_since(self, since) -> list[int]:
            self.since_arg = since
            return [5, 6]

    box = _UnstarredBox()
    names = PublicationNames(by_address={}, by_list_id={})

    documents = fetch_unstarred(box, date(2026, 9, 1), names)

    assert len(documents) == 2
    assert all(document.title == "This Week" for document in documents)
    assert box.since_arg == date(2026, 9, 1)


def test_fetch_unstarred_reports_how_many_it_found(capsys) -> None:
    """The unstarred scan silently walks up to ~90 more messages after the
    starred fetch - it must report the window's start date and how many
    it found, the same as the starred search does."""
    from datetime import date

    from newsprint.cli import fetch_unstarred
    from newsprint.config import PublicationNames

    class _TwoUnstarredBox(_FakeBox):
        def search_unflagged_since(self, since) -> list[int]:
            self.since_arg = since
            return [5, 6]

    box = _TwoUnstarredBox()
    names = PublicationNames(by_address={}, by_list_id={})

    fetch_unstarred(box, date(2026, 9, 1), names)

    output = capsys.readouterr().out
    assert "2 unstarred message(s) found since 1 Sep 2026" in output


def test_fetch_unstarred_uses_peek_so_nothing_is_marked_seen() -> None:
    """The unstarred picker only needs From/Subject/Date/List-Id - fetching
    with plain BODY[...] would mark every scanned message \\Seen as a side
    effect, silently mutating up to ~90 of the user's unread messages a
    week. PEEK is what prevents that: BODY.PEEK[...] fetches without
    setting \\Seen, where a plain BODY[...] would. This asserts the items
    string fetch_unstarred actually sends names PEEK explicitly, not just
    that some items string was sent."""
    from datetime import date

    from newsprint.cli import fetch_unstarred
    from newsprint.config import PublicationNames

    class _UnstarredBox(_FakeBox):
        def search_unflagged_since(self, since) -> list[int]:
            return [5, 6]

    box = _UnstarredBox()
    fetch_unstarred(
        box, date(2026, 9, 1), PublicationNames(by_address={}, by_list_id={})
    )

    assert len(box.fetch_many_calls) == 1
    uids, items = box.fetch_many_calls[0]
    assert uids == (5, 6)
    assert "PEEK" in items
    assert "HEADER.FIELDS" in items
    for field in ("FROM", "SUBJECT", "DATE", "LIST-ID"):
        assert field in items


def test_fetch_picked_uses_peek_free_full_items() -> None:
    """Unlike the listing scan, fetching what the user actually picked
    must retrieve the real body - RFC822, not a headers-only PEEK - since
    it is about to be built into the printed packet."""
    from newsprint.cli import fetch_picked
    from newsprint.config import PublicationNames

    box = _FakeBox()
    fetch_picked(box, [1, 2], PublicationNames(by_address={}, by_list_id={}))

    assert box.fetch_many_calls == [((1, 2), "(UID RFC822)")]


def test_password_never_appears_in_the_output(monkeypatch, mail_config) -> None:
    """Never print the IMAP password: only the host and user may appear in
    the "Connecting to..." line, never the secret used to log in."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr(
        "newsprint.cli.password_for", lambda host, user: "S3cr3t-Passw0rd!"
    )
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )

    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(mail_config.path)]
    )
    assert result.exit_code == 0
    assert "S3cr3t-Passw0rd!" not in result.output


def test_retire_printed_reports_progress_before_connecting(
    monkeypatch, mail_config, capsys
) -> None:
    """Retiring re-opens a second, writable connection - it must announce
    what it is about to do rather than go silent again."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    retire_printed(mail_config, [4, 7], "INBOX/Trash")

    output = capsys.readouterr().out
    assert "Retiring 2 message(s) to INBOX/Trash" in output


def test_retire_printed_moves_messages_with_no_failures(
    monkeypatch, mail_config
) -> None:
    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    asked: list[tuple[str, str]] = []

    def recording_password_for(host: str, user: str) -> str:
        asked.append((host, user))
        return "secret"

    monkeypatch.setattr("newsprint.cli.password_for", recording_password_for)

    result = retire_printed(mail_config, [4], "INBOX/Trash")

    assert _FakeBox.instances[0].retire_calls == [((4,), "INBOX/Trash")]
    assert result == RetireResult(retired=(), failed=())
    # This is the one call that moves mail, so it has to move it out of
    # the account and the folder the run actually read from.
    kwargs = _FakeBox.instances[0].kwargs
    assert kwargs["host"] == mail_config.mail.host
    assert kwargs["user"] == mail_config.mail.user
    assert kwargs["folder"] == mail_config.mail.folder
    assert kwargs["password"] == "secret"
    assert asked == [(mail_config.mail.host, mail_config.mail.user)]


def test_retire_printed_reports_a_partial_failure(
    monkeypatch, mail_config, capsys
) -> None:
    """A message that could not be retired must be named, not silently lost."""

    class _PartialFailureBox(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.retire_result = RetireResult(retired=(4,), failed=(7,))

    monkeypatch.setattr("newsprint.cli.Mailbox", _PartialFailureBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    result = retire_printed(mail_config, [4, 7], "INBOX/Trash")

    captured = capsys.readouterr()
    assert "could not retire 1 message(s)" in captured.err
    assert result == RetireResult(retired=(4,), failed=(7,))


def test_retire_printed_warns_distinctly_about_an_unrecoverable_message(
    monkeypatch, mail_config, capsys
) -> None:
    """A message whose failed MOVE's rescue re-star also failed is gone
    from the star-based queue with nothing visible to the user in
    Thunderbird - the one state they cannot discover on their own. It
    must get its own warning, distinct from the ordinary "could not
    retire" line, naming it as needing manual attention."""

    class _UnrecoverableBox(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.retire_result = RetireResult(
                retired=(), failed=(7,), unrecoverable=(7,)
            )

    monkeypatch.setattr("newsprint.cli.Mailbox", _UnrecoverableBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    result = retire_printed(mail_config, [7], "INBOX/Trash")

    captured = capsys.readouterr()
    assert "could not retire 1 message(s)" in captured.err
    assert "manual attention" in captured.err
    assert "(7,)" in captured.err
    assert result == RetireResult(retired=(), failed=(7,), unrecoverable=(7,))


def test_a_partial_retire_failure_is_reported_accurately(
    monkeypatch, tmp_path: Path
) -> None:
    """main()'s stdout must not claim more retirements than actually
    happened. Before the fix, main() echoed `len(uids)` - the number
    attempted - regardless of how many of those the mailbox actually
    reported as retired, contradicting its own stderr line about the
    failure in the same breath."""

    class _PartialFailureBox(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.retire_result = RetireResult(retired=(4,), failed=(7,))

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: (
            [
                _queued(identifier="<d@example.com>", uid=4),
                _queued("<g@example.com>", 7),
            ],
            "INBOX/Trash",
        ),
    )
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")
    monkeypatch.setattr("newsprint.cli.Mailbox", _PartialFailureBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    assert "Retired 1 message(s)" in result.output
    assert "Retired 2 message(s)" not in result.output
    assert "could not retire 1 message(s)" in result.output


def test_preview_open_failure_does_not_crash_the_run(
    monkeypatch, tmp_path: Path
) -> None:
    """subprocess.run(..., check=False) only suppresses a non-zero exit
    code; it still raises OSError when the command itself does not exist,
    which crashed every run on any non-macOS host. A host with neither
    `open` nor `xdg-open` must not crash: the PDF's own path is already
    echoed, which is enough to open it by hand."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )

    def missing(command, **kw):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("newsprint.cli.subprocess.run", missing)

    result = CliRunner().invoke(
        main, ["--dry-run", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code == 0


def test_preview_falls_back_to_xdg_open_when_open_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    attempted: list[list[str]] = []

    def fake_run(command, **kw):
        attempted.append(command)
        if command[0] == "open":
            raise FileNotFoundError("open")

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.subprocess.run", fake_run)

    result = CliRunner().invoke(
        main, ["--dry-run", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code == 0
    assert attempted[0][0] == "open"
    assert attempted[1][0] == "xdg-open"


def test_the_progress_bar_leaves_no_artifacts_in_captured_output(
    monkeypatch, tmp_path: Path
) -> None:
    """click.progressbar hides its bar rendering when the output is not a
    terminal - which is always true under CliRunner - so the existing
    string-matching assertions elsewhere in this file must keep working
    undisturbed by carriage returns, fill characters, or brackets from the
    bar itself."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "\r" not in result.output
    assert "[" not in result.output
    assert "#" not in result.output


def test_a_failing_document_among_others_is_still_reported_not_swallowed(
    monkeypatch, tmp_path: Path
) -> None:
    """The progress bar wraps the per-document build loop; a Failure in the
    middle of that loop must still surface exactly as it did before, not
    get lost because the bar consumed the iteration."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued(), _empty()], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "SKIPPED" in result.output
    assert "Empty Weekly" in result.output
    assert "Test Weekly" in result.output  # the document that did succeed


def test_contents_non_convergence_is_reported_not_silently_shipped(
    monkeypatch, tmp_path: Path
) -> None:
    """A contents page nobody can trust is worse than none: when
    build_contents cannot settle on a fixed point, main() must say so and
    still finish the run without a contents page, rather than crash or
    silently print numbers that might be wrong."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_contents",
        lambda built, config, date, out_dir, summary_cells=0: (None, False),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "could not compute reliable starting cell numbers" in result.output
    assert "omitting the contents page" in result.output


def test_the_finishing_phase_is_reported_in_order(monkeypatch, tmp_path: Path) -> None:
    """Contents, stamping, and imposing were the one stretch left silent
    after the fetch and build bars were added - measured at up to several
    seconds with nothing printed. Each must announce itself, in the order
    it actually happens, so the packet total never appears out of a
    multi-second silence."""
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    contents_at = result.output.index("Building the contents page")
    stamping_at = result.output.index("Stamping")
    imposing_at = result.output.index("Imposing")
    total_at = result.output.index("cells - ")
    assert contents_at < stamping_at < imposing_at < total_at


def test_a_successful_run_opens_the_pdf_in_preview(monkeypatch, tmp_path: Path) -> None:
    """Without --no-preview, main() must open the built PDF - but the open
    call itself is faked, so no real window is ever spawned during tests."""
    opened: list[list[str]] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.subprocess.run",
        lambda command, **kw: opened.append(command),
    )

    result = CliRunner().invoke(
        main, ["--dry-run", "--config", str(tmp_path / "absent.toml")]
    )

    assert result.exit_code == 0
    assert len(opened) == 1
    assert opened[0][:2] == ["open", "-a"]


def test_summary_disabled_by_default_never_touches_build_summary_pages(
    monkeypatch, tmp_path: Path
) -> None:
    """The tracked default is [summary].enabled = false - main() must not
    even call build_summary_pages, let alone read a key or hit the
    network, so a run with no such config section behaves exactly as it
    did before this feature existed. But unlike before, the run must now
    say plainly that the summary pages were skipped - the user's original
    complaint was silence, not just the missing pages."""

    def explode(*args, **kwargs):
        raise AssertionError("build_summary_pages must not be called when disabled")

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.build_summary_pages", explode)

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "Summary: disabled" in result.output


def test_summary_flag_enables_it_even_though_config_says_off(
    monkeypatch, tmp_path: Path
) -> None:
    """--summary must override a config default of false - the flag wins
    over the config file, matching --no-preview/--no-retire's precedence."""
    from newsprint.summarize import SummaryOutcome

    calls: list[object] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: (
            calls.append(True)
            or SummaryOutcome(
                pages=(),
                reason="no API key: stub",
                elapsed_seconds=0.1,
                input_tokens=None,
                output_tokens=None,
            )
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--summary",
            "--dry-run",
            "--no-preview",
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0
    assert calls == [True]
    assert "Summary skipped: no API key" in result.output


def test_no_summary_flag_disables_it_even_though_config_says_on(
    monkeypatch, tmp_path: Path
) -> None:
    """--no-summary must override a config default of true - and the run
    must still say clearly that summaries were skipped."""

    def explode(*args, **kwargs):
        raise AssertionError("build_summary_pages must not be called with --no-summary")

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.cli.build_summary_pages", explode)

    result = CliRunner().invoke(
        main,
        [
            "--no-summary",
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    assert "Summary: disabled" in result.output


def test_summary_flag_absent_lets_config_decide(monkeypatch, tmp_path: Path) -> None:
    """With neither --summary nor --no-summary given, the config file's
    [summary].enabled value must be the one that decides - the same
    behavior as before these flags existed."""
    from newsprint.summarize import SummaryOutcome

    calls: list[object] = []
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: (
            calls.append(True)
            or SummaryOutcome(
                pages=(),
                reason="no API key: stub",
                elapsed_seconds=0.1,
                input_tokens=None,
                output_tokens=None,
            )
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    assert calls == [True]


def test_help_explains_summary_needs_a_key_and_adds_time() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--summary" in result.output
    assert "--no-summary" in result.output
    assert "API key" in result.output
    assert "time" in result.output


def _summary_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text("[summary]\nenabled = true\n")
    return path


def test_summary_enabled_success_inserts_pages_and_reports_cost(
    monkeypatch, tmp_path: Path
) -> None:
    """A successful call must insert the returned pages into the packet
    (shifting cell counts and sheets accordingly) and report elapsed time
    and token counts on stdout."""
    import pymupdf

    from newsprint.summarize import SummaryOutcome

    def fake_page(name: str) -> Path:
        path = tmp_path / f"{name}.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=200, height=300)
            page.insert_text((20, 20), name)
            pdf.save(path)
        return path

    from newsprint.models import Document, Origin, Verdict
    from newsprint.pipeline import Built

    def fake_built(name: str) -> Built:
        return Built(
            document=Document(
                origin=Origin(kind="url", identifier=name),
                publication="Summary",
                title=name,
                date=_queued().date,
                html="<p>x</p>",
            ),
            pdf=fake_page(name),
            cells=1,
            verdict=Verdict.FULL,
        )

    summary_pages = (fake_built("summary-topics"), fake_built("summary-candidates"))

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=summary_pages,
            reason=None,
            elapsed_seconds=2.5,
            input_tokens=4000,
            output_tokens=150,
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    assert "Summary: 2 page(s) in 2.5s" in result.output
    assert "4000 in / 150 out tokens" in result.output
    assert "Summary skipped" not in result.output


def test_summary_success_without_token_counts_omits_the_token_clause(
    monkeypatch, tmp_path: Path
) -> None:
    """Not every SDK response exposes usage - the reported line must still
    make sense (page count and elapsed time) without a dangling token
    clause when input_tokens/output_tokens are unavailable."""
    import pymupdf

    from newsprint.models import Document, Origin, Verdict
    from newsprint.pipeline import Built
    from newsprint.summarize import SummaryOutcome

    path = tmp_path / "summary-topics.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page(width=200, height=300)
        pdf.save(path)
    page = Built(
        document=Document(
            origin=Origin(kind="url", identifier="summary-topics"),
            publication="Summary",
            title="Topics",
            date=_queued().date,
            html="<p>x</p>",
        ),
        pdf=path,
        cells=1,
        verdict=Verdict.FULL,
    )

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=(page,),
            reason=None,
            elapsed_seconds=1.5,
            input_tokens=None,
            output_tokens=None,
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    assert "Summary: 1 page(s) in 1.5s" in result.output
    assert "tokens" not in result.output


def test_summary_failure_is_reported_on_stdout_and_the_packet_still_prints(
    monkeypatch, tmp_path: Path
) -> None:
    """Degradation is not optional: any failure must still let the packet
    print, with one clear line on stdout (not stderr) saying why."""
    from newsprint.summarize import SummaryOutcome

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=(),
            reason="no API key: /tmp/nowhere.env does not exist",
            elapsed_seconds=0.01,
            input_tokens=None,
            output_tokens=None,
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    assert "Summary skipped: no API key" in result.output
    # Reported on stdout, not stderr - CliRunner mixes them by default, so
    # assert directly against the exception-free, successful completion
    # instead: the run must finish and still produce a packet.
    assert "Dry run" in result.output


def test_an_empty_summary_outcome_prints_no_summary_line_at_all(
    monkeypatch, tmp_path: Path
) -> None:
    """A successful call that honestly found nothing worth reporting (both
    lists empty) is not a failure and must not be announced as either a
    success or a skip - it simply adds nothing."""
    from newsprint.summarize import SummaryOutcome

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=(),
            reason=None,
            elapsed_seconds=1.0,
            input_tokens=500,
            output_tokens=10,
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    assert "Summary" not in result.output


_TOTAL_CELLS_RE = re.compile(r"(\d+) cells - ")


def test_summary_pages_shift_the_contents_starting_numbers(
    monkeypatch, tmp_path: Path
) -> None:
    """End to end: two 1-cell summary pages ahead of the contents-numbered
    newsletters must add exactly 2 to the packet's total cell count versus
    the same run with summary disabled - the fixed-point interaction the
    spec calls out as the riskiest part of this feature."""
    import pymupdf

    from newsprint.models import Document, Origin, Verdict
    from newsprint.pipeline import Built
    from newsprint.summarize import SummaryOutcome

    def fake_page(name: str) -> Path:
        path = tmp_path / f"{name}.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page(width=200, height=300)
            pdf.save(path)
        return path

    def fake_built(name: str) -> Built:
        return Built(
            document=Document(
                origin=Origin(kind="url", identifier=name),
                publication="Summary",
                title=name,
                date=_queued().date,
                html="<p>x</p>",
            ),
            pdf=fake_page(name),
            cells=1,
            verdict=Verdict.FULL,
        )

    summary_pages = (fake_built("summary-topics"), fake_built("summary-candidates"))

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )

    baseline = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert baseline.exit_code == 0
    baseline_cells = int(_TOTAL_CELLS_RE.search(baseline.output).group(1))

    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=summary_pages,
            reason=None,
            elapsed_seconds=0.1,
            input_tokens=1,
            output_tokens=1,
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--config",
            str(_summary_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0
    summary_cells = int(_TOTAL_CELLS_RE.search(result.output).group(1))
    assert summary_cells == baseline_cells + 2


# ---------------------------------------------------------------------------
# The unstarred-newsletter picker (phase6-picker.md)
# ---------------------------------------------------------------------------


def test_no_pick_flag_never_calls_fetch_unstarred(monkeypatch, mail_config) -> None:
    """--no-pick must skip the picker branch of fetch_queue entirely -
    fetch_unstarred (and so its underlying search) must never run.
    Unit-tests fetch_queue directly, since main()-level tests always
    mock fetch_queue away and so cannot exercise its own no_pick gate."""

    def explode(box, since, names):
        raise AssertionError("fetch_unstarred must not be called with --no-pick")

    monkeypatch.setattr("newsprint.cli.fetch_unstarred", explode)
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    documents, _trash = fetch_queue(mail_config, no_pick=True)
    assert len(documents) == 2  # the starred fetch still ran normally


def test_an_unconfigured_picker_is_skipped_with_a_message_not_a_crash(
    monkeypatch, mail_config
) -> None:
    """A problem specific to the picker's own step on the shared
    connection - here, the unstarred SEARCH itself failing mid-session -
    must not abort a run that already has a valid starred queue: the
    starred queue this run was already going to build must still print,
    exactly like a summary failure degrades (H3) rather than aborting.

    Before the two connections were merged into one, this scenario was
    "no mail account configured" specifically for the picker's own,
    separate connect attempt. Now that the picker shares fetch_queue's
    already-open connection, config.require_mail() gates the whole run
    once, up front - so "unconfigured" can no longer happen only for the
    picker. A mid-session IMAP failure on the shared connection is the
    realistic scenario that replaces it."""
    from newsprint.mail import MailError

    class _FailingUnstarredBox(_QueueBox):
        def search_unflagged_since(self, since):
            raise MailError("server dropped the connection")

    monkeypatch.setattr("newsprint.cli.Mailbox", _FailingUnstarredBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(mail_config.path)],
    )
    assert result.exit_code == 0
    assert "Could not check for unstarred newsletters" in result.output
    assert "Dry run" in result.output


def test_dry_run_shows_the_unstarred_window_and_skips_the_prompt(
    monkeypatch, mail_config
) -> None:
    """Verify bullet: 'The live queue in dry-run: how many unstarred
    messages the window finds, and that the list renders sensibly
    grouped.' Nothing shown here may be built - dry-run always skips the
    selection prompt (stdin is not a terminal under CliRunner, so no
    _stdin_is_tty override is needed to take that branch)."""

    class _Box(_QueueBox):
        unstarred_uids = (20,)
        extra_messages: ClassVar[dict[int, bytes]] = {
            20: _candidate_raw(20, "Money Stuff", "Extra Issue")
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(mail_config.path)],
    )
    assert result.exit_code == 0
    assert "Money Stuff" in result.output
    assert "Extra Issue" in result.output
    assert "dry run" in result.output.lower()
    # Shown, but never built: the build report line has a colon after the
    # publication name ("Test Weekly: N cells"); the listing's group
    # heading never does.
    assert "Money Stuff: " not in result.output


def _pick_by_title(*titles: str):
    """A fake questionary_prompt: picks documents by title out of
    whatever Picklist cli.py actually built, standing in for
    space-toggling specific rows in the real checkbox. Injected exactly
    like printer.spool's `runner` - see pickerui.py's own module
    docstring."""

    def fake(picklist):
        by_title = {
            row.document.title: row.document
            for group in picklist.groups
            for row in group.rows
        }
        return [by_title[title] for title in titles]

    return fake


def _pick_nothing(picklist):
    """A fake questionary_prompt standing in for confirming the checkbox
    with nothing checked (or cancelling - both mean "add nothing")."""
    return []


def test_dry_run_still_prompts_when_stdin_is_a_terminal(
    monkeypatch, mail_config
) -> None:
    """--dry-run is a preview, not a non-interactive mode: the point of
    showing the unstarred list is letting the user add to it, so a dry
    run with a real terminal on stdin must still prompt, and a selected
    pick must still be built into the preview (just not printed or
    retired). Only non-interactive stdin should skip the prompt."""

    class _Box(_QueueBox):
        unstarred_uids = (21,)
        extra_messages: ClassVar[dict[int, bytes]] = {
            21: _candidate_raw(21, "Alpha Weekly", "Pick One", buildable=True)
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("newsprint.cli.questionary_prompt", _pick_by_title("Pick One"))

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(mail_config.path)],
    )
    assert result.exit_code == 0
    assert "Choose newsletters to add" in result.output
    assert "Added 1 newsletter(s)" in result.output
    assert "Alpha Weekly: " in result.output  # the pick was actually built
    assert "Dry run" in result.output


def test_a_non_interactive_run_skips_the_prompt_and_does_not_hang(
    monkeypatch, mail_config
) -> None:
    """No --dry-run here, and no `input=` is given at all - if the prompt
    were reached despite stdin not being a terminal, CliRunner's stdin
    would be exhausted and the run would abort or hang rather than finish
    cleanly with exit_code 0. The starred queue is deliberately empty
    here (unlike _QueueBox's default), so "Nothing starred" is the only
    thing left to assert once the picker itself has been skipped."""

    class _Box(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.messages = {20: _candidate_raw(20, "Money Stuff")}

        def search_flagged(self) -> list[int]:
            return []

        def search_unflagged_since(self, since) -> list[int]:
            return [20]

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(mail_config.path)]
    )
    assert result.exit_code == 0
    assert "not a terminal" in result.output.lower()
    assert "Nothing starred" in result.output


def test_no_candidates_in_the_window_says_so_and_skips_the_prompt(
    monkeypatch, mail_config
) -> None:
    monkeypatch.setattr("newsprint.cli.Mailbox", _QueueBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )

    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(mail_config.path)]
    )
    assert result.exit_code == 0
    assert "No unstarred newsletters since" in result.output


def test_a_capped_non_interactive_listing_says_how_many_were_omitted(
    monkeypatch, mail_config
) -> None:
    """Past DISPLAY_LIMIT, the non-interactive text fallback caps the
    listing and says so - the flood-protection DISPLAY_LIMIT exists for
    (see picker.py's own docstring). The interactive checkbox does not
    apply this cap at all, since it is genuinely scrollable."""
    from newsprint.picker import DISPLAY_LIMIT

    count = DISPLAY_LIMIT + 5
    uids = list(range(100, 100 + count))

    class _Box(_QueueBox):
        unstarred_uids = tuple(uids)
        extra_messages: ClassVar[dict[int, bytes]] = {
            uid: _candidate_raw(uid, "Daily Thing", f"Issue {uid}") for uid in uids
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )

    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(mail_config.path)]
    )
    assert result.exit_code == 0
    assert f"{count} found; showing the most recent {DISPLAY_LIMIT}." in result.output


def test_selecting_two_rows_adds_exactly_those_newsletters(
    monkeypatch, mail_config, tmp_path: Path
) -> None:
    """Verify bullet: 'Selecting several newsletters adds exactly those
    to the packet.' 'Charlie Weekly' is deliberately left unchecked and
    must not be built."""

    class _Box(_QueueBox):
        unstarred_uids = (21, 22, 23)
        extra_messages: ClassVar[dict[int, bytes]] = {
            21: _candidate_raw(21, "Alpha Weekly", "Pick One", buildable=True),
            22: _candidate_raw(22, "Bravo Weekly", "Pick Two", buildable=True),
            23: _candidate_raw(23, "Charlie Weekly", "Not Picked", buildable=True),
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "newsprint.cli.questionary_prompt", _pick_by_title("Pick One", "Pick Two")
    )
    # Declining the print confirm below still calls runlog.record("cancelled")
    # for real - route it to tmp_path, not the user's actual state directory.
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(mail_config.path)],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Alpha Weekly: " in result.output
    assert "Bravo Weekly: " in result.output
    assert "Charlie Weekly: " not in result.output
    assert "Added 2 newsletter(s)" in result.output


def test_selected_picks_shift_the_contents_starting_numbers(
    monkeypatch, mail_config, tmp_path: Path
) -> None:
    """End to end: a pick must join the exact same contents-numbering
    pipeline a starred document does, with no special case. Both picks
    below render identical bodies, so each contributes the same number of
    cells; picking both must add exactly twice what picking just the
    first one adds - proof the second pick's presence shifts the
    contents' starting cell numbers by its own real size, not some fixed
    or wrong amount.
    """

    class _Box(_QueueBox):
        unstarred_uids = (21, 22)
        extra_messages: ClassVar[dict[int, bytes]] = {
            21: _candidate_raw(21, "Alpha Weekly", "Pick One", buildable=True),
            22: _candidate_raw(22, "Bravo Weekly", "Pick Two", buildable=True),
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    # Both non-dry-run invocations below decline the print confirm, which
    # calls runlog.record("cancelled") for real - route it to tmp_path, not
    # the user's actual state directory.
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    baseline = CliRunner().invoke(
        main,
        [
            "--no-pick",
            "--dry-run",
            "--no-preview",
            "--config",
            str(mail_config.path),
        ],
    )
    assert baseline.exit_code == 0
    baseline_cells = int(_TOTAL_CELLS_RE.search(baseline.output).group(1))

    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("newsprint.cli.questionary_prompt", _pick_by_title("Pick One"))

    one_pick = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(mail_config.path)],
        input="n\n",
    )
    assert one_pick.exit_code == 0
    one_pick_cells = int(_TOTAL_CELLS_RE.search(one_pick.output).group(1))
    per_pick = one_pick_cells - baseline_cells
    assert per_pick > 0  # the pick must actually have been built, not skipped

    monkeypatch.setattr(
        "newsprint.cli.questionary_prompt",
        _pick_by_title("Pick One", "Pick Two"),
    )
    two_picks = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(mail_config.path)],
        input="n\n",
    )
    assert two_picks.exit_code == 0
    two_pick_cells = int(_TOTAL_CELLS_RE.search(two_picks.output).group(1))
    assert two_pick_cells == baseline_cells + 2 * per_pick


def test_confirming_with_nothing_checked_skips_selection(
    monkeypatch, mail_config, tmp_path: Path
) -> None:
    """The checkbox equivalent of the old numbered list's blank Enter:
    confirming with nothing checked (or cancelling - questionary_prompt
    returns [] or None for both) adds nothing to the packet."""

    class _Box(_QueueBox):
        unstarred_uids = (21,)
        extra_messages: ClassVar[dict[int, bytes]] = {
            21: _candidate_raw(21, "Alpha Weekly", "Pick", buildable=True)
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("newsprint.cli.questionary_prompt", _pick_nothing)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(mail_config.path)],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Alpha Weekly: " not in result.output


def test_a_picked_newsletter_is_retired_like_any_other(
    monkeypatch, mail_config, tmp_path: Path
) -> None:
    """The retirement invariant: a newsletter picked from the unstarred
    list and printed is retired exactly like a starred one - no special
    case, because `uids` is derived only from `built`, which the pick now
    belongs to just like every other document.

    _candidate_raw's fixed Date (4 Sep 2026) is earlier than _STARRED_RAW's
    (5 Sep 2026), so once fetch_queue merges by date (see cli.py's own
    docstring on why), the pick sorts before the starred document -
    [99, 4], not [4, 99]. That is the fix under test here, not
    incidental: the old starred-then-picked order would have hidden a
    regression back to it."""

    class _Box(_QueueBox):
        unstarred_uids = (99,)
        extra_messages: ClassVar[dict[int, bytes]] = {
            99: _candidate_raw(99, "Alpha Weekly", "Pick", buildable=True)
        }

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("newsprint.cli.questionary_prompt", _pick_by_title("Pick"))
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")

    retired_uids: list[list[int]] = []

    def fake_retire_printed(config, uids, trash):
        retired_uids.append(uids)
        return RetireResult(retired=tuple(uids), failed=())

    monkeypatch.setattr("newsprint.cli.retire_printed", fake_retire_printed)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(mail_config.path)],
        input="y\n",
    )
    assert result.exit_code == 0
    assert retired_uids == [[99, 4]]  # picked (4 Sep) before starred (5 Sep)


def test_picker_trash_is_used_when_the_starred_queue_was_empty(
    monkeypatch, mail_config, tmp_path: Path
) -> None:
    """fetch_queue skips discovering Trash when nothing is starred (trash
    is None) - but a pick can still introduce a uid that needs retiring.
    The Trash folder the picker itself discovered must be used, or a
    printed pick would never be retired."""

    class _Box(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.messages = {
                99: _candidate_raw(99, "Alpha Weekly", "Pick", buildable=True)
            }

        def search_flagged(self) -> list[int]:
            return []

        def search_unflagged_since(self, since) -> list[int]:
            return [99]

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("newsprint.cli.questionary_prompt", _pick_by_title("Pick"))
    monkeypatch.setattr("newsprint.cli.spool", lambda pdf, config: "Printer-1")

    retire_calls: list[tuple] = []

    def fake_retire_printed(config, uids, trash):
        retire_calls.append((uids, trash))
        return RetireResult(retired=tuple(uids), failed=())

    monkeypatch.setattr("newsprint.cli.retire_printed", fake_retire_printed)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(mail_config.path)],
        input="y\n",
    )
    assert result.exit_code == 0
    assert retire_calls == [([99], "INBOX/Trash")]


def _dated_raw(uid: int, publication: str, title: str, when: str) -> bytes:
    return _message(
        f"""
From: {publication} <pub{uid}@example.com>
Subject: {title}
Date: {when}
Message-ID: <{uid}@example.com>
Content-Type: text/html; charset="utf-8"
""",
        f"<div><p>{LONG_PROSE}</p></div>",
    )


def test_fetch_queue_merges_a_pick_between_two_starred_documents_by_date(
    monkeypatch, mail_config
) -> None:
    """Picked newsletters must land in ascending date order alongside the
    starred ones, not appended after them in a block - the user's own
    complaint was entries 4-222 in date order, then the picks clumped in
    afterwards at 228, 235, 246... The starred pair here (1 Sep, 5 Sep)
    bracket the pick's own date (3 Sep); the previous starred-then-picked
    behavior would have put the pick last regardless of its date."""

    class _Box(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.messages = {
                1: _dated_raw(
                    1, "Alpha Weekly", "First", "Tue, 1 Sep 2026 08:00:00 +0000"
                ),
                2: _dated_raw(
                    2, "Charlie Weekly", "Middle Pick", "Thu, 3 Sep 2026 08:00:00 +0000"
                ),
                3: _dated_raw(
                    3, "Zulu Weekly", "Last", "Sat, 5 Sep 2026 08:00:00 +0000"
                ),
            }

        def search_flagged(self) -> list[int]:
            return [1, 3]

        def search_unflagged_since(self, since) -> list[int]:
            return [2]

    monkeypatch.setattr("newsprint.cli.Mailbox", _Box)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.window_since",
        lambda fallback_days, today: date(2026, 9, 1),
    )
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "newsprint.cli.questionary_prompt", _pick_by_title("Middle Pick")
    )

    documents, _trash = fetch_queue(mail_config, no_pick=False)
    assert [document.title for document in documents] == [
        "First",
        "Middle Pick",
        "Last",
    ]


def test_a_pick_between_two_starred_documents_appears_in_order_on_the_contents_page(
    tmp_path: Path,
) -> None:
    """The other half of the same fix, checked as its own derivation:
    build_contents renders whatever order `built` arrives in (see
    test_contents.py), and cli.py now derives `built`'s order from
    fetch_queue's own date-sorted merge - so a pick landing between two
    starred documents by date must show up between them on the contents
    page too, not just in the build report. Two separate checks (this
    one and test_fetch_queue_merges_a_pick_between_two_starred_documents_
    by_date above) because they are two separate derivations of the same
    sequence - one could regress without the other."""
    from datetime import datetime

    from newsprint.contents import build_contents
    from newsprint.models import Document, Origin, Verdict
    from newsprint.pdfutil import page_text
    from newsprint.pipeline import Built

    def _built(uid: int, publication: str, title: str, day: str) -> Built:
        document = Document(
            origin=Origin(kind="email", identifier=f"<{uid}@example.com>", uid=uid),
            publication=publication,
            title=title,
            date=datetime.fromisoformat(day).replace(tzinfo=UTC),
            html="<p>x</p>",
        )
        return Built(
            document=document, pdf=Path("/dev/null"), cells=1, verdict=Verdict.FULL
        )

    # Already in the order fetch_queue's date-sorted merge would produce -
    # this test is about build_contents/render honoring that order, not
    # about the merge itself (covered above).
    built = [
        _built(1, "Alpha Weekly", "First", "2026-09-01"),
        _built(2, "Charlie Weekly", "Middle Pick", "2026-09-03"),
        _built(3, "Zulu Weekly", "Last", "2026-09-05"),
    ]
    config = load_config(tmp_path / "absent.toml")
    result, converged = build_contents(built, config, date(2026, 9, 5), tmp_path)
    assert converged
    assert result is not None
    text = page_text(result.pdf, 0)
    rows = re.findall(
        r"^(\d+)\s+(Alpha Weekly|Charlie Weekly|Zulu Weekly)", text, re.MULTILINE
    )
    assert [publication for _cell, publication in rows] == [
        "Alpha Weekly",
        "Charlie Weekly",
        "Zulu Weekly",
    ]


def test_help_mentions_no_pick() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--no-pick" in result.output


def test_the_summary_announces_itself_before_the_wait(
    monkeypatch, tmp_path: Path
) -> None:
    """The summary is a single Claude API call covering every newsletter
    in the packet - 17.9s on the user's own queue, 160k tokens in - and it
    was the last multi-second stretch that printed nothing until it was
    already over. It has to say what it is doing first, like every other
    step of the finishing phase does.
    """
    from newsprint.summarize import SummaryOutcome

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=(),
            reason="no API key",
            elapsed_seconds=0.1,
            input_tokens=None,
            output_tokens=None,
        ),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(_summary_config(tmp_path))],
    )
    assert result.exit_code == 0
    notice_at = result.output.index("Writing the summary pages")
    outcome_at = result.output.index("Summary skipped")
    assert notice_at < outcome_at


def test_the_summary_notice_warns_that_it_takes_a_while(
    monkeypatch, tmp_path: Path
) -> None:
    """Saying "writing" is not enough on its own: without a hint that the
    wait is expected, a twenty-second pause reads as a hang."""
    from newsprint.summarize import SummaryOutcome

    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr(
        "newsprint.cli.build_summary_pages",
        lambda built, config, packet_date, out_dir: SummaryOutcome(
            pages=(),
            reason="no API key",
            elapsed_seconds=0.1,
            input_tokens=None,
            output_tokens=None,
        ),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(_summary_config(tmp_path))],
    )
    notice = next(
        line for line in result.output.splitlines() if "Writing the summary" in line
    )
    assert "moment" in notice


def test_no_summary_notice_when_summaries_are_disabled(tmp_path: Path) -> None:
    """Nothing is about to happen, so nothing should be announced."""
    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--no-summary",
            "--config",
            str(tmp_path / "a.toml"),
        ],
    )
    assert "Writing the summary" not in result.output


def _no_mail(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: ([_queued()], "INBOX/Trash"),
    )
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")


def test_output_writes_the_packet_to_a_named_file(monkeypatch, tmp_path: Path) -> None:
    """Without --output the packet lands in a temp directory under a
    random name, which is fine for a run that prints immediately and
    useless for one that hands you a PDF."""
    _no_mail(monkeypatch, tmp_path)
    destination = tmp_path / "reading" / "this-week.pdf"

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--output",
            str(destination),
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0
    assert destination.is_file(), result.output
    assert destination.stat().st_size > 0
    assert str(destination) in result.output


def test_output_to_a_directory_names_the_file_by_date(
    monkeypatch, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    _no_mail(monkeypatch, tmp_path)
    folder = tmp_path / "packets"
    folder.mkdir()

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--output",
            str(folder),
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0
    today = datetime.now(UTC).date().isoformat()
    written = list(folder.glob("*.pdf"))
    assert len(written) == 1, result.output
    # Dated, and stamped with the hour so a second build the same day
    # sits beside the first rather than replacing it.
    assert written[0].name.startswith(f"newsprint-{today}-")
    assert re.fullmatch(rf"newsprint-{today}-\d{{4}}\.pdf", written[0].name)


def test_no_print_asks_before_retiring_and_never_spools(
    monkeypatch, tmp_path: Path
) -> None:
    """When the tool does not print, only the user knows whether paper
    came out - so it asks, rather than assuming either way."""
    events: list[str] = []
    _no_mail(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "newsprint.cli.spool",
        lambda pdf, config: events.append("spool") or "Printer-1",
    )

    def fake_retire_printed(config, uids, trash):
        events.append(f"retire:{uids}")
        return RetireResult(retired=tuple(uids), failed=())

    monkeypatch.setattr("newsprint.cli.retire_printed", fake_retire_printed)

    result = CliRunner().invoke(
        main,
        ["--no-print", "--no-preview", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert result.exit_code == 0
    assert events == ["retire:[4]"], "must retire without ever spooling"


def test_no_print_declined_leaves_mail_untouched(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    _no_mail(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "newsprint.cli.spool",
        lambda pdf, config: events.append("spool") or "Printer-1",
    )
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: events.append("retire"),
    )

    result = CliRunner().invoke(
        main,
        ["--no-print", "--no-preview", "--config", str(tmp_path / "absent.toml")],
        input="n\n",
    )
    assert result.exit_code == 0
    assert events == []
    assert "untouched" in result.output


def test_no_print_with_no_retire_never_asks(monkeypatch, tmp_path: Path) -> None:
    """--no-retire is already an explicit "leave the mail alone", so there
    is nothing left to ask about."""
    events: list[str] = []
    _no_mail(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "newsprint.cli.retire_printed",
        lambda config, uids, trash: events.append("retire"),
    )

    result = CliRunner().invoke(
        main,
        [
            "--no-print",
            "--no-retire",
            "--no-preview",
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0
    assert events == []
    assert "Printed" not in result.output or "?" not in result.output


def test_dry_run_is_exactly_no_print_plus_no_retire(
    monkeypatch, tmp_path: Path
) -> None:
    """--dry-run is documented as equivalent to --no-print --no-retire, and
    they are separate code paths, so the equivalence is asserted rather
    than trusted: two branches that must agree will otherwise drift.
    """

    def observe(flags: list[str], where: Path) -> tuple[int, dict, bool]:
        events: dict[str, object] = {"spool": 0, "retire": 0, "runlog": []}
        monkeypatch.setattr(
            "newsprint.cli.fetch_queue",
            lambda config, no_pick: ([_queued()], "INBOX/Trash"),
        )
        monkeypatch.setattr(
            "newsprint.cli.spool",
            lambda pdf, config: (
                events.__setitem__("spool", events["spool"] + 1) or "J-1"
            ),
        )
        monkeypatch.setattr(
            "newsprint.cli.retire_printed",
            lambda config, uids, trash: events.__setitem__(
                "retire", events["retire"] + 1
            ),
        )
        monkeypatch.setattr(
            "newsprint.runlog.record",
            lambda entry, **kw: (
                events["runlog"].append(entry.get("outcome"))  # type: ignore[union-attr]
                or where / "r"
            ),
        )
        pdf = where / "packet.pdf"
        result = CliRunner().invoke(
            main,
            [
                *flags,
                "--no-preview",
                "--output",
                str(pdf),
                "--config",
                str(where / "absent.toml"),
            ],
        )
        return result.exit_code, events, pdf.is_file()

    dry = observe(["--dry-run"], tmp_path / "dry")
    both = observe(["--no-print", "--no-retire"], tmp_path / "both")

    assert dry == both, f"--dry-run {dry} diverged from --no-print --no-retire {both}"
    assert dry[1] == {"spool": 0, "retire": 0, "runlog": []}
    assert dry[2], "both must still build the PDF"


def test_version_reports_release_project_page_and_author() -> None:
    """Three lines: what this is, where it lives, who wrote it."""
    from importlib.metadata import version as installed_version

    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0] == f"newsprint {installed_version('newsprint')}"
    assert lines[1] == "https://pypi.org/project/newsprint/"
    assert lines[2].startswith("Reuven Lerner <")


def test_the_version_is_not_hardcoded_in_the_source() -> None:
    """Read from the installed metadata, so it cannot drift from
    pyproject.toml the way the README's Python version did."""
    from importlib.metadata import version as installed_version

    from newsprint import cli

    assert installed_version("newsprint") not in Path(cli.__file__).read_text()


def test_help_carries_the_same_three_lines() -> None:
    """A person reaching for --help should not have to also know that
    --version exists to find out who wrote this or where it lives."""
    from importlib.metadata import version as installed_version

    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert f"newsprint {installed_version('newsprint')}" in result.output
    assert "https://pypi.org/project/newsprint/" in result.output
    assert "Reuven Lerner <" in result.output


def _retirement_log(tmp_path: Path, **overrides) -> Path:
    """A run-log directory holding one "retired" entry."""
    import json

    directory = tmp_path / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "outcome": "retired",
        "at": "2026-09-10T12:00:00+00:00",
        "folder": "INBOX/toprint",
        "trash": "INBOX/Trash",
        "retired_ids": ["<a@x>", "<b@x>"],
        "retired": [4, 5],
    }
    entry.update(overrides)
    (directory / "2026-09-10-120000-000000.json").write_text(json.dumps(entry))
    return directory


def test_unretire_restores_the_last_retirement(monkeypatch, tmp_path: Path) -> None:
    from newsprint.mail import UnretireResult

    # A folder deliberately unlike the configured one: the entry records
    # where these messages actually came from, and a run made against a
    # different folder has to send them back there rather than to
    # whatever the config happens to say today.
    directory = _retirement_log(tmp_path, folder="INBOX/lastweek")
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")
    seen: dict[str, object] = {}

    def recording_password_for(host: str, user: str) -> str:
        seen["credentials"] = (host, user)
        return "secret"

    monkeypatch.setattr("newsprint.cli.password_for", recording_password_for)

    class FakeBox:
        def __init__(self, **kwargs):
            seen["mailbox_kwargs"] = kwargs
            seen["folder"] = kwargs["folder"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def unretire(self, message_ids, trash):
            seen["ids"] = list(message_ids)
            seen["trash"] = trash
            return UnretireResult(restored=tuple(message_ids))

    monkeypatch.setattr("newsprint.cli.Mailbox", FakeBox)

    result = CliRunner().invoke(
        main,
        ["--unretire", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert seen["ids"] == ["<a@x>", "<b@x>"]
    assert seen["trash"] == "INBOX/Trash"
    assert seen["folder"] == "INBOX/lastweek"
    assert "Restored 2 message(s) to INBOX/lastweek" in result.output
    # The line naming which retirement is about to be undone carries its
    # timestamp, so two of them can be told apart.
    assert "Last retirement (2026-09-10T12:00:00+00:00)" in result.output
    assert "still marked read" in result.output
    # The mailbox is opened with the account from the config and the
    # folder the retirement came out of - not the configured folder, if
    # the run happened against a different one - and the keychain is
    # asked under that same account.
    config = load_config(tmp_path / "absent.toml")
    assert seen["credentials"] == (config.mail.host, config.mail.user)
    kwargs = seen["mailbox_kwargs"]
    assert kwargs["host"] == config.mail.host
    assert kwargs["user"] == config.mail.user
    assert kwargs["password"] == "secret"


def test_unretire_declined_changes_nothing(monkeypatch, tmp_path: Path) -> None:
    directory = _retirement_log(tmp_path)
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)

    def explode(**kwargs):
        raise AssertionError("no mailbox may be opened when the user declines")

    monkeypatch.setattr("newsprint.cli.Mailbox", explode)

    result = CliRunner().invoke(
        main, ["--unretire", "--config", str(tmp_path / "absent.toml")], input="n\n"
    )
    assert result.exit_code == 0
    assert "Nothing changed" in result.output


def test_unretire_with_no_retirement_logged_says_so(
    monkeypatch, tmp_path: Path
) -> None:
    empty = tmp_path / "runs"
    empty.mkdir()
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", empty)
    result = CliRunner().invoke(
        main, ["--unretire", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code != 0
    assert "nothing to undo" in result.output


def test_unretire_on_a_log_predating_the_feature_explains_itself(
    monkeypatch, tmp_path: Path
) -> None:
    """Entries written before --unretire recorded uids but no Message-IDs,
    and a uid names nothing once the message has moved."""
    directory = _retirement_log(tmp_path, retired_ids=[])
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)
    result = CliRunner().invoke(
        main, ["--unretire", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code != 0
    assert "by hand" in result.output


def test_unretire_reports_what_it_could_not_restore(
    monkeypatch, tmp_path: Path
) -> None:
    from newsprint.mail import UnretireResult

    directory = _retirement_log(tmp_path)
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    class PartialBox:
        def __init__(self, **kwargs) -> None: ...
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def unretire(self, message_ids, trash):
            return UnretireResult(
                restored=("<a@x>",), missing=("<b@x>",), failed=("<c@x>",)
            )

    monkeypatch.setattr("newsprint.cli.Mailbox", PartialBox)
    result = CliRunner().invoke(
        main,
        ["--unretire", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert result.exit_code == 0
    assert "Restored 1 message(s)" in result.output
    assert "1 not found in INBOX/Trash" in result.output
    assert "1 could not be moved back" in result.output
    # Both of those are warnings and belong on stderr: what goes to
    # stdout is the run's own account of itself, which a reader may well
    # be piping somewhere.
    assert "not found in" in result.stderr
    assert "could not be moved back" in result.stderr
    assert "Restored 1 message(s)" not in result.stderr


def test_unretire_turns_a_mail_error_into_a_clean_message(
    monkeypatch, tmp_path: Path
) -> None:
    directory = _retirement_log(tmp_path)
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    from newsprint.mail import MailError

    def explode(**kwargs):
        raise MailError("the trash folder is not selectable")

    monkeypatch.setattr("newsprint.cli.Mailbox", explode)
    result = CliRunner().invoke(
        main, ["--unretire", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert "not selectable" in result.output
    assert "Traceback" not in result.output


def test_setup_flag_runs_the_wizard_and_builds_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    called: list[Path] = []
    monkeypatch.setattr("newsprint.cli.run_setup", lambda path: called.append(path))
    monkeypatch.setattr(
        "newsprint.cli.fetch_queue",
        lambda config, no_pick: (_ for _ in ()).throw(
            AssertionError("--setup must not fetch mail")
        ),
    )
    target = tmp_path / "config.toml"
    result = CliRunner().invoke(main, ["--setup", "--config", str(target)])
    assert result.exit_code == 0
    assert called == [target]


def test_unretire_needs_a_yes_and_not_just_an_enter(monkeypatch, tmp_path) -> None:
    """Moving mail back is the mirror of retiring it, and both are hard to
    undo - so the question is asked with "no" as its default. Someone
    running --unretire to see what it would do, and pressing return out of
    habit, must not move anything."""
    directory = _retirement_log(tmp_path)
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)

    def explode(**kwargs):
        raise AssertionError("no mailbox may be opened on a bare return")

    monkeypatch.setattr("newsprint.cli.Mailbox", explode)

    result = CliRunner().invoke(
        main, ["--unretire", "--config", str(tmp_path / "absent.toml")], input="\n"
    )
    assert result.exit_code == 0
    assert "Nothing changed" in result.output


def test_unretire_falls_back_to_the_configured_folder(monkeypatch, tmp_path) -> None:
    """A retirement logged before the folder was recorded still knows
    where its messages went; where they come back to is whatever the
    config says now."""
    from newsprint.mail import UnretireResult

    directory = _retirement_log(tmp_path, folder="")
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)
    monkeypatch.setattr("newsprint.runlog.record", lambda entry, **kw: tmp_path / "r")
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    seen: dict[str, object] = {}

    class FakeBox:
        def __init__(self, **kwargs):
            seen["folder"] = kwargs["folder"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def unretire(self, message_ids, trash):
            return UnretireResult(restored=tuple(message_ids))

    monkeypatch.setattr("newsprint.cli.Mailbox", FakeBox)
    result = CliRunner().invoke(
        main,
        ["--unretire", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert seen["folder"] == load_config(tmp_path / "absent.toml").mail.folder


def test_unretire_records_what_it_restored(monkeypatch, tmp_path) -> None:
    """The undo is itself logged, so a reader can see what came back and
    what did not - and so a second --unretire finds an "unretired" entry
    rather than the retirement it has already undone."""
    from newsprint.mail import UnretireResult

    directory = _retirement_log(tmp_path)
    monkeypatch.setattr("newsprint.runlog.DEFAULT_STATE_DIR", directory)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    recorded: list[dict[str, object]] = []
    monkeypatch.setattr(
        "newsprint.runlog.record",
        lambda entry, **kw: (recorded.append(entry), tmp_path / "r")[1],
    )

    class FakeBox:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def unretire(self, message_ids, trash):
            return UnretireResult(restored=("<a@x>",), missing=("<b@x>",))

    monkeypatch.setattr("newsprint.cli.Mailbox", FakeBox)
    CliRunner().invoke(
        main,
        ["--unretire", "--config", str(tmp_path / "absent.toml")],
        input="y\n",
    )
    assert recorded == [
        {
            "outcome": "unretired",
            "folder": "INBOX/toprint",
            "trash": "INBOX/Trash",
            "restored": ["<a@x>"],
            "missing": ["<b@x>"],
            "failed": [],
        }
    ]


def test_about_reads_every_line_from_the_installed_metadata() -> None:
    """--version builds its message at import time, so the function
    itself needs exercising directly. All three lines come from the
    package metadata rather than the source, which is what keeps them
    from drifting from pyproject.toml."""
    from importlib.metadata import metadata
    from importlib.metadata import version as installed_version

    from newsprint.cli import about

    lines = about().splitlines()
    assert lines == [
        f"newsprint {installed_version('newsprint')}",
        "https://pypi.org/project/newsprint/",
        str(metadata("newsprint")["Author-email"]),
    ]
    assert "@" in lines[2], "the author line must carry a real address"


def test_a_fetch_reports_the_time_it_took_not_the_time_of_day(
    monkeypatch, mail_config
) -> None:
    """The fetch prints how long it took, and a clock read straight out
    of time.monotonic() is a plausible-looking positive number - it is
    the machine's uptime, which on a laptop left running reads as days."""
    import re

    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")

    runner = CliRunner()
    with runner.isolation() as (out, _err, _):
        fetch_queue(mail_config, no_pick=True)
        printed = out.getvalue().decode()

    seconds = [float(match) for match in re.findall(r"in (\d+\.\d\d)s\.", printed)]
    assert seconds, f"no timing line in {printed!r}"
    assert all(value < 60.0 for value in seconds), printed


def test_a_fetch_applies_the_readers_own_publication_names(
    monkeypatch, mail_config
) -> None:
    """publications.toml is how a reader renames a newsletter whose own
    idea of its name is unhelpful, and the overrides have to reach the
    extractor to have any effect. Dropped on the way, every rename in
    that file silently does nothing."""
    from newsprint.config import PublicationNames

    _FakeBox.instances.clear()
    monkeypatch.setattr("newsprint.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("newsprint.cli.password_for", lambda host, user: "secret")
    monkeypatch.setattr(
        "newsprint.cli.load_publication_names",
        lambda: PublicationNames(
            by_address={"news@example.com": "The Renamed Weekly"}, by_list_id={}
        ),
    )

    documents, _trash = fetch_queue(mail_config, no_pick=True)

    assert [document.publication for document in documents] == [
        "The Renamed Weekly",
        "The Renamed Weekly",
    ]


# ---------------------------------------------------------------------------
# _offer_picks: the unstarred review list. Called directly here rather
# than through the whole command, so the listing it prints and the
# arguments it threads are both visible.
# ---------------------------------------------------------------------------


def _offer(
    box, mail_config, since=date(2026, 9, 1), today=date(2026, 9, 5), names=None
):
    """Run _offer_picks against a fake box, returning (result, output)."""
    from newsprint.cli import _offer_picks
    from newsprint.config import PublicationNames

    runner = CliRunner()
    with runner.isolation() as (out, _err, _):
        result = _offer_picks(
            box,
            mail_config,
            names or PublicationNames(by_address={}, by_list_id={}),
            since,
            today,
        )
        printed = out.getvalue().decode()
    return result, printed


def test_an_empty_review_window_names_the_date_it_looked_back_to(
    monkeypatch, mail_config
) -> None:
    """Nothing to offer is worth saying, and worth saying *since when* -
    "no unstarred newsletters" on its own leaves the reader wondering
    whether the window was a day or a month."""

    class _Empty(_QueueBox):
        unstarred_uids = ()

    (picked, uids), printed = _offer(_Empty(), mail_config, since=date(2026, 9, 1))
    assert (picked, uids) == ([], [])
    assert "No unstarred newsletters since 1 Sep" in printed


def test_the_review_list_dates_its_rows_against_the_run_date(
    monkeypatch, mail_config
) -> None:
    """ "today" and "yesterday" are the two labels a reader actually reads,
    and they only mean anything relative to the day the run is happening -
    which the caller supplies rather than the picker reading a clock."""

    class _Box(_QueueBox):
        unstarred_uids = (21,)
        extra_messages: ClassVar[dict[int, bytes]] = {
            21: _candidate_raw(21, "Alpha Weekly", "Pick One"),
        }

    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: False)
    # The candidate is dated 4 September; a run on the 5th makes it
    # yesterday's, and a run on the 4th makes it today's.
    _result, on_the_fifth = _offer(_Box(), mail_config, today=date(2026, 9, 5))
    _result, on_the_fourth = _offer(_Box(), mail_config, today=date(2026, 9, 4))
    assert "yesterday" in on_the_fifth
    assert "today" in on_the_fourth
    # Each row also carries how long the newsletter is, which is the
    # other thing a reader chooses on. That takes a size per candidate,
    # so a listing whose sizes were never fetched reads "length unknown"
    # against every row while looking otherwise complete.
    assert "length unknown" not in on_the_fifth
    assert "[short]" in on_the_fifth


def test_a_review_list_that_fits_is_not_described_as_a_sample(
    monkeypatch, mail_config
) -> None:
    """Two different sentences: "N found" when the reader is seeing all of
    them, and "N found; showing the most recent M" when they are not. A
    list that exactly fills the cap is the whole list, not a sample of
    itself."""

    class _Box(_QueueBox):
        unstarred_uids = tuple(range(21, 21 + DISPLAY_LIMIT))
        extra_messages: ClassVar[dict[int, bytes]] = {
            uid: _candidate_raw(uid, f"Paper {uid}", f"Issue {uid}")
            for uid in range(21, 21 + DISPLAY_LIMIT)
        }

    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: False)
    _result, printed = _offer(_Box(), mail_config)
    assert f"{DISPLAY_LIMIT} unstarred newsletter(s) found." in printed
    assert "showing the most recent" not in printed


def test_a_review_list_too_long_to_show_says_how_many_it_kept(
    monkeypatch, mail_config
) -> None:
    """One past the cap, and the reader is told both numbers - otherwise
    a newsletter they were looking for is simply absent with no
    explanation."""
    total = DISPLAY_LIMIT + 1

    class _Box(_QueueBox):
        unstarred_uids = tuple(range(21, 21 + total))
        extra_messages: ClassVar[dict[int, bytes]] = {
            uid: _candidate_raw(uid, f"Paper {uid}", f"Issue {uid}")
            for uid in range(21, 21 + total)
        }

    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: False)
    _result, printed = _offer(_Box(), mail_config)
    assert f"{total} found; showing the most recent {DISPLAY_LIMIT}." in printed


def test_a_picked_newsletter_gets_the_readers_own_publication_name(
    monkeypatch, mail_config
) -> None:
    """The rename overrides have to reach the second fetch as well as the
    first. Dropped here, a picked newsletter prints under whatever name
    its sender chose while a starred one prints under the reader's."""
    from newsprint.config import PublicationNames

    class _Box(_QueueBox):
        unstarred_uids = (21,)
        extra_messages: ClassVar[dict[int, bytes]] = {
            21: _candidate_raw(21, "Alpha Weekly", "Pick One", buildable=True),
        }

    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("newsprint.cli.questionary_prompt", _pick_by_title("Pick One"))
    names = PublicationNames(
        by_address={"pub21@example.com": "The Renamed Weekly"}, by_list_id={}
    )
    (picked, uids), _printed = _offer(_Box(), mail_config, names=names)
    assert uids == [21]
    assert [document.publication for document in picked] == ["The Renamed Weekly"]

    # And the scan behind the listing, not just the fetch behind the
    # pick. The listing is what the reader reads while choosing, so a
    # rename taking effect only afterwards shows them the wrong name at
    # the one moment it matters. (The listing is printed on the
    # non-interactive path; the interactive one hands straight to the
    # prompt.)
    monkeypatch.setattr("newsprint.cli._stdin_is_tty", lambda: False)
    _result, listed = _offer(_Box(), mail_config, names=names)
    assert "The Renamed Weekly" in listed
    assert "Alpha Weekly" not in listed


# ---------------------------------------------------------------------------
# What the finished packet is called.
# ---------------------------------------------------------------------------


def test_a_packet_is_named_for_its_date_and_the_hour_it_was_built() -> None:
    """The file a reader ends up holding - in Preview, in their Downloads
    folder - is named after what it is. "sheets.pdf" tells them nothing,
    and two of them in one folder tell them less than nothing.

    The time is in there because a packet can be built twice in a day: a
    print that jams, or a second look after starring one more thing."""
    from newsprint.cli import packet_filename

    assert (
        packet_filename(
            "", date(2026, 9, 11), datetime(2026, 9, 11, 14, 32, tzinfo=UTC)
        )
        == "newsprint-2026-09-11-1432.pdf"
    )


def test_a_packet_title_names_the_file_when_there_is_one() -> None:
    """packet.title is what the reader calls this thing, so it is what
    the file should be called too - reduced to something a file name can
    hold without quoting."""
    from newsprint.cli import packet_filename

    assert (
        packet_filename(
            "Family Shabbat Reading",
            date(2026, 9, 11),
            datetime(2026, 9, 11, 8, 5, tzinfo=UTC),
        )
        == "family-shabbat-reading-2026-09-11-0805.pdf"
    )


def test_a_title_that_is_all_punctuation_falls_back_to_the_tool_name() -> None:
    """Nothing survives the reduction, so there is no name in it to use -
    and a file called "-2026-09-11-1432.pdf" is worse than one that says
    what built it."""
    from newsprint.cli import packet_filename

    assert (
        packet_filename(
            "!!! ???", date(2026, 9, 11), datetime(2026, 9, 11, 14, 32, tzinfo=UTC)
        )
        == "newsprint-2026-09-11-1432.pdf"
    )


def test_a_very_long_title_is_cut_to_a_usable_length() -> None:
    """A title is free text and a file name is not. The cut lands on a
    word boundary rather than mid-word, and never leaves a trailing
    dash."""
    from newsprint.cli import packet_filename

    name = packet_filename(
        "The Very Long Weekly Reading Packet For All Of Us Here At Home",
        date(2026, 9, 11),
        datetime(2026, 9, 11, 14, 32, tzinfo=UTC),
    )
    stem = name.removesuffix("-2026-09-11-1432.pdf")
    assert len(stem) <= 40
    assert not stem.endswith("-")
    assert name.endswith("-2026-09-11-1432.pdf")


# ---------------------------------------------------------------------------
# Where packets live when --output says nothing, and how long they stay.
# ---------------------------------------------------------------------------


def test_a_packet_with_no_output_lands_somewhere_it_will_survive(
    monkeypatch, tmp_path: Path
) -> None:
    """A temp directory is the wrong home for the one artifact of a run:
    spool() returning success means CUPS took the job, not that paper
    came out right, and by the time you know otherwise the mail is
    retired and the OS has the PDF."""
    _no_mail(monkeypatch, tmp_path)
    packets = tmp_path / "packets"
    config = tmp_path / "config.toml"
    config.write_text(f'[output]\ndirectory = "{packets}"\n')

    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    written = list(packets.glob("*.pdf"))
    assert len(written) == 1, result.output
    assert str(written[0]) in result.output


def test_old_packets_are_swept_when_a_new_one_is_built(
    monkeypatch, tmp_path: Path
) -> None:
    """Kept forever, the directory becomes another thing to tidy. The
    sweep runs when a packet is written, removes only PDFs, and says what
    it took - every other removal in this tool is reported."""
    import os
    import time

    _no_mail(monkeypatch, tmp_path)
    packets = tmp_path / "packets"
    packets.mkdir()
    stale = packets / "newsprint-2026-01-01-0900.pdf"
    stale.write_bytes(b"%PDF-1.4 old")
    fresh = packets / "newsprint-2026-09-10-0900.pdf"
    fresh.write_bytes(b"%PDF-1.4 recent")
    not_a_packet = packets / "notes.txt"
    not_a_packet.write_text("mine")
    long_ago = time.time() - 60 * 60 * 24 * 45
    for path in (stale, not_a_packet):
        os.utime(path, (long_ago, long_ago))

    config = tmp_path / "config.toml"
    config.write_text(f'[output]\ndirectory = "{packets}"\nkeep_days = 30\n')
    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    assert not stale.exists(), "a packet past the limit goes"
    assert fresh.exists(), "one inside it stays"
    assert not_a_packet.exists(), "anything that is not a packet is left alone"
    assert "1 packet" in result.output


def test_keeping_packets_forever_is_available(monkeypatch, tmp_path: Path) -> None:
    """Zero days means no sweep at all, for anyone who would rather keep
    the lot and tidy it themselves."""
    import os
    import time

    _no_mail(monkeypatch, tmp_path)
    packets = tmp_path / "packets"
    packets.mkdir()
    ancient = packets / "newsprint-2020-01-01-0900.pdf"
    ancient.write_bytes(b"%PDF-1.4 ancient")
    long_ago = time.time() - 60 * 60 * 24 * 4000
    os.utime(ancient, (long_ago, long_ago))

    config = tmp_path / "config.toml"
    config.write_text(f'[output]\ndirectory = "{packets}"\nkeep_days = 0\n')
    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert ancient.exists()


def test_an_explicit_output_directory_is_never_swept(
    monkeypatch, tmp_path: Path
) -> None:
    """--output names a directory of the reader's own. Deleting things
    out of it because they are old is not this tool's business."""
    import os
    import time

    _no_mail(monkeypatch, tmp_path)
    mine = tmp_path / "mine"
    mine.mkdir()
    old = mine / "newsprint-2020-01-01-0900.pdf"
    old.write_bytes(b"%PDF-1.4 ancient")
    long_ago = time.time() - 60 * 60 * 24 * 4000
    os.utime(old, (long_ago, long_ago))

    result = CliRunner().invoke(
        main,
        [
            "--dry-run",
            "--no-preview",
            "--output",
            str(mine),
            "--config",
            str(tmp_path / "absent.toml"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert old.exists()


def test_a_packet_that_will_not_delete_does_not_stop_the_run(
    monkeypatch, tmp_path: Path
) -> None:
    """Tidying is the least important thing a run does. A packet that
    cannot be removed - a permission, a file open in a viewer, a
    disappearing network mount - is left where it is and offered again
    next time, rather than taking the newsletters down with it."""
    import os
    import time

    _no_mail(monkeypatch, tmp_path)
    packets = tmp_path / "packets"
    packets.mkdir()
    stuck = packets / "newsprint-2020-01-01-0900.pdf"
    stuck.write_bytes(b"%PDF-1.4 old")
    long_ago = time.time() - 60 * 60 * 24 * 4000
    os.utime(stuck, (long_ago, long_ago))

    real_unlink = Path.unlink

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == stuck.name:
            raise PermissionError(self)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)

    config = tmp_path / "config.toml"
    config.write_text(f'[output]\ndirectory = "{packets}"\nkeep_days = 30\n')
    result = CliRunner().invoke(
        main, ["--dry-run", "--no-preview", "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    assert stuck.exists(), "left where it is"
    assert "Swept" not in result.output, "and not counted as swept"
