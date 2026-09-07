from datetime import UTC
from pathlib import Path
from typing import ClassVar, Self

import pytest
from click.testing import CliRunner

from shabbat_print.cli import fetch_queue, main, retire_printed
from shabbat_print.config import load_config
from shabbat_print.mail import RetireResult

SAMPLE_CONFIG = """
[mail]
host = "imap.example.com"
user = "someone@example.com"
folder = "INBOX/toprint"
"""


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
    """Stands in for shabbat_print.mail.Mailbox in unit tests for
    fetch_queue and retire_printed, so those functions can be exercised
    without ever opening a real IMAP connection."""

    instances: ClassVar[list[_FakeBox]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.retire_calls: list[tuple] = []
        self.retire_result = RetireResult(retired=(), failed=())
        _FakeBox.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def search_flagged(self) -> list[int]:
        return [1, 2]

    def fetch(self, uid: int) -> bytes:
        return RAW_MESSAGE

    def trash_folder(self) -> str:
        return "INBOX/Trash"

    def retire(self, uids, trash_folder):
        self.retire_calls.append((tuple(uids), trash_folder))
        return self.retire_result


@pytest.fixture
def mail_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE_CONFIG)
    return load_config(path)


def test_help_names_the_paper_switch() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--paper" in result.output


def test_an_empty_queue_says_so(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shabbat_print.cli.fetch_queue", lambda config: ([], None))
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "absent.toml")])
    assert result.exit_code == 0
    assert "Nothing starred" in result.output


def test_dry_run_never_prints_and_never_retires(monkeypatch, tmp_path: Path) -> None:
    """--dry-run must build a real PDF and then stop, leaving mail untouched.

    The queue must be non-empty, or the command returns early and the test
    passes without ever reaching the --dry-run branch.
    """
    from datetime import datetime

    from shabbat_print.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<a@example.com>", uid=1),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<div><p>The Federal Reserve declined to move rates this "
        "month, which surprised almost nobody.</p></div>",
    )

    spooled: list[Path] = []
    retired: list[list[int]] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([document], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.spool", lambda pdf, config: spooled.append(pdf)
    )
    monkeypatch.setattr(
        "shabbat_print.cli.retire_printed",
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

    from shabbat_print.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<b@example.com>", uid=2),
        publication="Test Weekly",
        title="Another Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<div><p>The Federal Reserve declined to move rates this "
        "month, which surprised almost nobody.</p></div>",
    )
    spooled: list[Path] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([document], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.spool", lambda pdf, config: spooled.append(pdf)
    )
    monkeypatch.setattr(
        "shabbat_print.runlog.record", lambda entry, **kw: tmp_path / "x"
    )

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

    from shabbat_print.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=uid),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<div><p>The Federal Reserve declined to move rates this "
        "month, which surprised almost nobody.</p></div>",
    )


def test_accepting_prints_then_retires_in_that_order(
    monkeypatch, tmp_path: Path
) -> None:
    """The invariant: mail is modified only after a job reaches the queue."""
    events: list[str] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_queued()], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.spool",
        lambda pdf, config: (events.append("spool"), "Printer-1")[1],
    )
    monkeypatch.setattr(
        "shabbat_print.cli.retire_printed",
        lambda config, uids, trash: events.append(f"retire:{uids}"),
    )
    monkeypatch.setattr(
        "shabbat_print.runlog.record", lambda entry, **kw: tmp_path / "r"
    )

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    assert events == ["spool", "retire:[4]"]


def test_a_print_failure_leaves_mail_untouched(monkeypatch, tmp_path: Path) -> None:
    from shabbat_print.printer import PrintError

    retired: list[list[int]] = []

    def explode(pdf, config):
        raise PrintError("lp: no such printer")

    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_queued()], "INBOX/Trash")
    )
    monkeypatch.setattr("shabbat_print.cli.spool", explode)
    monkeypatch.setattr(
        "shabbat_print.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )
    monkeypatch.setattr(
        "shabbat_print.runlog.record", lambda entry, **kw: tmp_path / "r"
    )

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert "no such printer" in result.output
    assert retired == []


def test_an_unconfigured_account_says_what_to_set(monkeypatch, tmp_path: Path) -> None:
    """With no config file at all, the error names the missing keys."""
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "absent.toml")])
    assert result.exit_code != 0
    assert "mail.host and mail.user" in result.output


def test_a_document_that_cannot_be_built_is_reported(
    monkeypatch, tmp_path: Path
) -> None:
    """A newsletter that cleans down to nothing is named, not silently lost."""
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_empty()], None)
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

    from shabbat_print.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier="<e@example.com>", uid=5),
        publication="Empty Weekly",
        title="Nothing",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<div><p>Unsubscribe</p></div>",
    )


def test_fetch_queue_extracts_the_flagged_messages(monkeypatch, mail_config) -> None:
    """Unit-tests fetch_queue's own body, which the CLI-level tests above
    always monkeypatch away: it must open the mailbox read-only, extract a
    Document per flagged uid, and discover the Trash folder."""
    _FakeBox.instances.clear()
    monkeypatch.setattr("shabbat_print.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("shabbat_print.cli.password_for", lambda host, user: "secret")

    documents, trash = fetch_queue(mail_config)

    assert len(documents) == 2
    assert all(document.title == "This Week" for document in documents)
    assert trash == "INBOX/Trash"
    assert _FakeBox.instances[0].kwargs["host"] == "imap.example.com"
    assert _FakeBox.instances[0].kwargs["password"] == "secret"


def test_fetch_queue_skips_trash_lookup_when_nothing_is_flagged(
    monkeypatch, mail_config
) -> None:
    """No point discovering the Trash folder for a run with nothing to retire."""

    class _EmptyBox(_FakeBox):
        def search_flagged(self) -> list[int]:
            return []

        def trash_folder(self) -> str:
            raise AssertionError("trash_folder must not be called for an empty queue")

    monkeypatch.setattr("shabbat_print.cli.Mailbox", _EmptyBox)
    monkeypatch.setattr("shabbat_print.cli.password_for", lambda host, user: "secret")

    documents, trash = fetch_queue(mail_config)
    assert documents == []
    assert trash is None


def test_retire_printed_moves_messages_with_no_failures(
    monkeypatch, mail_config
) -> None:
    _FakeBox.instances.clear()
    monkeypatch.setattr("shabbat_print.cli.Mailbox", _FakeBox)
    monkeypatch.setattr("shabbat_print.cli.password_for", lambda host, user: "secret")

    retire_printed(mail_config, [4], "INBOX/Trash")

    assert _FakeBox.instances[0].retire_calls == [((4,), "INBOX/Trash")]


def test_retire_printed_reports_a_partial_failure(
    monkeypatch, mail_config, capsys
) -> None:
    """A message that could not be retired must be named, not silently lost."""

    class _PartialFailureBox(_FakeBox):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.retire_result = RetireResult(retired=(4,), failed=(7,))

    monkeypatch.setattr("shabbat_print.cli.Mailbox", _PartialFailureBox)
    monkeypatch.setattr("shabbat_print.cli.password_for", lambda host, user: "secret")

    retire_printed(mail_config, [4, 7], "INBOX/Trash")

    captured = capsys.readouterr()
    assert "could not retire 1 message(s)" in captured.err


def test_a_successful_run_opens_the_pdf_in_preview(monkeypatch, tmp_path: Path) -> None:
    """Without --no-preview, main() must open the built PDF - but the open
    call itself is faked, so no real window is ever spawned during tests."""
    opened: list[list[str]] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_queued()], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.subprocess.run",
        lambda command, **kw: opened.append(command),
    )

    result = CliRunner().invoke(
        main, ["--dry-run", "--config", str(tmp_path / "absent.toml")]
    )

    assert result.exit_code == 0
    assert len(opened) == 1
    assert opened[0][:2] == ["open", "-a"]
