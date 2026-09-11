"""Setup wizard tests.

Every prompt is injected, so none of this touches a terminal, a keyring
or a mail server - the same discipline printer.spool's `runner` and
mail.Mailbox's `imap_factory` already use.
"""

from pathlib import Path
from typing import ClassVar

import pytest

from newsprint.mail import MailError
from newsprint.setup import run_setup


class FakeBox:
    """A mailbox that signs in and reports a handful of folders."""

    folders_found: ClassVar[list[str]] = ["INBOX", "INBOX.toprint", "INBOX.Trash"]

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def folders(self):
        return list(self.folders_found)


def _answers(*values: str):
    """An `ask` that returns each value in turn."""
    remaining = iter(values)

    def ask(question: str, **kwargs) -> str:
        return next(remaining)

    return ask


def test_setup_writes_a_config_and_stores_the_password(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    stored: list[tuple[str, str, str]] = []

    run_setup(
        path,
        ask=_answers("imap.example.com", "someone@example.com", "hunter2", ""),
        confirm=lambda *a, **k: True,
        choose=lambda question, options, default: (
            "INBOX.toprint" if "Folder" in question else "A4"
        ),
        echo=lambda message: None,
        open_mailbox=lambda **kwargs: FakeBox(**kwargs),
        store_password=lambda host, user, password: stored.append(
            (host, user, password)
        ),
    )

    written = path.read_text()
    assert 'host    = "imap.example.com"' in written
    # The plural, which is the setting - it takes one name as readily as
    # a list, and a config written today should use the name that is
    # documented rather than the one kept working for older ones.
    assert 'folders = "INBOX.toprint"' in written
    assert 'paper   = "A4"' in written
    assert stored == [("imap.example.com", "someone@example.com", "hunter2")]
    assert "hunter2" not in written, "the password never goes in the config file"


def test_setup_offers_the_servers_own_folder_names(tmp_path: Path) -> None:
    """The delimiter is not standardized and a Gmail folder is a label, so
    the list comes from the server rather than from the user's guess."""
    offered: list[list[str]] = []

    def choose(question: str, options, default: str) -> str:
        offered.append(list(options))
        return default

    run_setup(
        tmp_path / "config.toml",
        ask=_answers("imap.example.com", "someone@example.com", "pw", ""),
        confirm=lambda *a, **k: True,
        choose=choose,
        echo=lambda message: None,
        open_mailbox=lambda **kwargs: FakeBox(**kwargs),
        store_password=lambda *a: None,
    )
    assert offered[0] == ["INBOX", "INBOX.toprint", "INBOX.Trash"]


def test_setup_checks_the_credentials_before_writing_anything(
    tmp_path: Path,
) -> None:
    """A config that looks right but cannot sign in is the failure this
    exists to prevent."""
    import click

    path = tmp_path / "config.toml"

    def refuse(**kwargs):
        raise MailError("authentication failed")

    with pytest.raises(click.ClickException, match="Could not sign in"):
        run_setup(
            path,
            ask=_answers("imap.example.com", "someone@example.com", "wrong", ""),
            confirm=lambda *a, **k: True,
            choose=lambda *a: "A4",
            echo=lambda message: None,
            open_mailbox=refuse,
            store_password=lambda *a: None,
        )
    assert not path.exists()


def test_setup_declines_to_replace_an_existing_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# mine\n")
    run_setup(
        path,
        ask=_answers(),
        confirm=lambda *a, **k: False,
        choose=lambda *a: "A4",
        echo=lambda message: None,
        open_mailbox=lambda **kwargs: FakeBox(**kwargs),
        store_password=lambda *a: None,
    )
    assert path.read_text() == "# mine\n"


def test_setup_warns_that_gmail_needs_an_app_password(tmp_path: Path) -> None:
    said: list[str] = []
    run_setup(
        tmp_path / "config.toml",
        ask=_answers("imap.gmail.com", "someone@gmail.com", "pw", ""),
        confirm=lambda *a, **k: True,
        choose=lambda question, options, default: default,
        echo=said.append,
        open_mailbox=lambda **kwargs: FakeBox(**kwargs),
        store_password=lambda *a: None,
    )
    assert any("app password" in line for line in said)


def test_the_folder_picker_filters_as_you_type(monkeypatch) -> None:
    """181 folders is not a question, it is a haystack - so the picker
    is an autocomplete over the server's own names."""
    import questionary

    from newsprint import setup as setup_module

    seen: dict[str, object] = {}

    class FakeQuestion:
        def ask(self):
            return "INBOX.toprint"

    def fake_autocomplete(question, choices, default):
        seen["question"] = question
        seen["choices"] = list(choices)
        return FakeQuestion()

    monkeypatch.setattr(questionary, "autocomplete", fake_autocomplete)
    chosen = setup_module._choose("  Folder", ["INBOX", "INBOX.toprint"], "INBOX")
    assert chosen == "INBOX.toprint"
    assert seen["choices"] == ["INBOX", "INBOX.toprint"]


def test_an_abandoned_folder_prompt_keeps_the_default(monkeypatch) -> None:
    """questionary returns None when the prompt is interrupted; that is a
    cancel, not a folder named None."""
    import questionary

    from newsprint import setup as setup_module

    class Cancelled:
        def ask(self):
            return None

    monkeypatch.setattr(questionary, "autocomplete", lambda *a, **k: Cancelled())
    assert setup_module._choose("  Folder", ["INBOX"], "INBOX") == "INBOX"


def test_setup_only_asks_before_replacing_something(tmp_path: Path) -> None:
    """The question guards an existing file. With nothing there to lose,
    a "no" to anything else must not be read as "write nothing" - the
    wizard would then decline to set itself up and say so for a reason
    that has not happened."""
    path = tmp_path / "config.toml"
    run_setup(
        path,
        ask=_answers("imap.example.com", "someone@example.com", "hunter2", ""),
        confirm=lambda *a, **k: False,
        choose=lambda *a: "A4",
        echo=lambda message: None,
        open_mailbox=lambda **kwargs: FakeBox(**kwargs),
        store_password=lambda *a: None,
    )
    assert path.exists()


def test_the_password_prompt_hides_what_is_typed(tmp_path: Path) -> None:
    """A password echoed to the terminal ends up in a scrollback buffer
    and, on a shared screen, in the room. The host prompt carries Gmail's
    own server as its default, since that is what most people setting
    this up are on."""
    asked: list[tuple[str, dict[str, object]]] = []
    remaining = iter(("imap.example.com", "someone@example.com", "hunter2", ""))

    def recording_ask(question: str, **kwargs: object) -> str:
        asked.append((question, kwargs))
        return next(remaining)

    run_setup(
        tmp_path / "config.toml",
        ask=recording_ask,
        confirm=lambda *a, **k: True,
        choose=lambda *a: "A4",
        echo=lambda message: None,
        open_mailbox=lambda **kwargs: FakeBox(**kwargs),
        store_password=lambda *a: None,
    )
    by_question = {question.strip(): kwargs for question, kwargs in asked}
    assert by_question["Password"] == {"hide_input": True}
    assert by_question["IMAP host"] == {"default": "imap.gmail.com"}
