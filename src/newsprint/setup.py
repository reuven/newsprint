"""Interactive first-run setup: write a config.toml that actually works.

Every question here is one someone would otherwise have to answer by
guessing. The folder in particular: the IMAP hierarchy delimiter is not
standardized, and on Gmail a "folder" is a label, so the name that looks
obvious is often not what the server calls it. Rather than explain that,
this connects and asks the server for the list.

The credentials are checked before anything is written. A config file
that looks right but cannot log in is the failure this exists to prevent.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import click
import keyring

from .mail import Mailbox, MailError

Ask = Callable[..., str]
Confirm = Callable[..., bool]
Choose = Callable[[str, Sequence[str], str], str]
Echo = Callable[[str], None]

_PAPERS = ("A4", "Letter")

_TEMPLATE = """\
# Written by `newsprint --setup`. See config.example.toml in the project
# for every option and what it does.

[mail]
host    = "{host}"
user    = "{user}"
folders = "{folder}"
trash   = "auto"

[print]
printer = "{printer}"
paper   = "{paper}"
duplex  = "two-sided-long-edge"
"""


def _choose(question: str, options: Sequence[str], default: str) -> str:
    """Pick one of `options`, with typing to filter when there are many.

    Servers really do have 181 folders; a numbered list of those is not a
    question, it is a haystack.
    """
    import questionary

    answer = questionary.autocomplete(
        question, choices=list(options), default=default
    ).ask()
    return str(answer) if answer else default


def run_setup(
    config_path: Path,
    *,
    ask: Ask = click.prompt,
    confirm: Confirm = click.confirm,
    choose: Choose = _choose,
    echo: Echo = click.echo,
    open_mailbox: Callable[..., Mailbox] = Mailbox,
    store_password: Callable[[str, str, str], None] = keyring.set_password,
) -> None:
    """Ask what is needed, verify it, and write the config."""
    if config_path.exists() and not confirm(
        f"{config_path} exists. Replace it?", default=False
    ):
        echo("Nothing written.")
        return

    echo("\nMail account")
    host = ask("  IMAP host", default="imap.gmail.com")
    user = ask("  Email address")
    if host.endswith("gmail.com"):
        echo(
            "  Gmail needs an app password, not your account password:\n"
            "  https://myaccount.google.com/apppasswords"
        )
    password = ask("  Password", hide_input=True)

    echo("\n  Checking those credentials...")
    try:
        with open_mailbox(
            host=host, user=user, password=password, folder="INBOX"
        ) as box:
            folders = box.folders()
    except MailError as error:
        raise click.ClickException(f"Could not sign in: {error}") from error
    echo(f"  Signed in. {len(folders)} folders.")

    echo("\nWhich folder do you star newsletters into?")
    folder = choose("  Folder", folders, "INBOX")

    echo("\nPrinting")
    printer = ask(
        "  Printer name (blank for your system default)", default="", show_default=False
    )
    paper = choose("  Paper", _PAPERS, "A4")

    store_password(host, user, password)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _TEMPLATE.format(
            host=host, user=user, folder=folder, printer=printer, paper=paper
        )
    )
    echo(f"\nWrote {config_path}")
    echo(f"Password stored in your keychain under {host} / {user}.")
    echo("\nStar a few newsletters, then run: newsprint --dry-run")
