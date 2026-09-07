"""The Friday afternoon command.

The order of operations enforces the design's one invariant: mail is modified
only after a job has reached the print queue, and only for the messages whose
content actually reached it.
"""

import subprocess
import tempfile
from pathlib import Path

import click

from . import runlog
from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    load_config,
    load_publication_names,
)
from .extract import extract
from .impose import impose
from .mail import Mailbox, MailError, RetireResult, password_for
from .models import Document, Verdict
from .pipeline import build
from .printer import PrintError, spool


def fetch_queue(config: Config) -> tuple[list[Document], str | None]:
    """Fetch the starred messages, read-only, and discover the Trash folder."""
    config.require_mail()
    password = password_for(config.mail.host, config.mail.user)
    names = load_publication_names()
    with Mailbox(
        host=config.mail.host,
        user=config.mail.user,
        password=password,
        folder=config.mail.folder,
    ) as box:
        uids = box.search_flagged()
        documents = [extract(box.fetch(uid), uid=uid, names=names) for uid in uids]
        trash = box.trash_folder() if uids else None
    return documents, trash


def retire_printed(config: Config, uids: list[int], trash: str) -> RetireResult:
    password = password_for(config.mail.host, config.mail.user)
    with Mailbox(
        host=config.mail.host,
        user=config.mail.user,
        password=password,
        folder=config.mail.folder,
    ) as box:
        result = box.retire(uids, trash)
    if result.failed:
        click.echo(
            f"  could not retire {len(result.failed)} message(s): {result.failed}",
            err=True,
        )
    return result


@click.command()
@click.option(
    "--paper",
    type=click.Choice(["a4", "letter"], case_sensitive=False),
    default=None,
    help="Paper size. Defaults to A4; use letter when printing in the US.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_PATH,
    help="Path to config.toml.",
)
@click.option(
    "--dry-run", is_flag=True, help="Build the PDF but do not print or retire."
)
@click.option(
    "--no-retire",
    is_flag=True,
    help=(
        "Print, but do not retire: unlike --dry-run, this really prints. "
        "The messages stay starred and will be reprinted on the next run."
    ),
)
@click.option("--no-preview", is_flag=True, help="Skip opening the PDF in Preview.")
def main(
    paper: str | None,
    config_path: Path,
    dry_run: bool,
    no_retire: bool,
    no_preview: bool,
) -> None:
    """Print this week's starred newsletters, four to a side, duplex."""
    config = load_config(config_path, paper_override=paper)

    try:
        documents, trash = fetch_queue(config)
    except (MailError, ConfigError) as error:
        raise click.ClickException(str(error)) from error

    if not documents:
        click.echo("Nothing starred in the queue.")
        return

    click.echo(f"Building {len(documents)} newsletters:")
    work_dir = Path(tempfile.mkdtemp(prefix="shabbat-print-"))
    built, failed = build(documents, config, work_dir)

    for item in built:
        note = {
            Verdict.FILLER: "  (filler cell dropped)",
            Verdict.WIDOW: "  (squeezed to fit)",
            Verdict.FULL: "",
        }[item.verdict]
        click.echo(f"  {item.document.publication}: {item.cells} cells{note}")
    for failure in failed:
        click.echo(
            f"  SKIPPED {failure.document.publication}: {failure.error}", err=True
        )

    if not built:
        click.echo("Nothing could be built.", err=True)
        return

    sheets_pdf = work_dir / "sheets.pdf"
    sides = impose([item.pdf for item in built], config.printing.paper, sheets_pdf)
    cells = sum(item.cells for item in built)
    click.echo(
        f"\n  {cells} cells - {sides} sheet sides on {config.printing.paper.name}"
    )
    click.echo(f"  {sheets_pdf}")

    if not no_preview:
        subprocess.run(["open", "-a", "Preview", str(sheets_pdf)], check=False)

    if dry_run:
        click.echo("\nDry run: nothing printed, nothing retired.")
        return

    destination = config.printing.printer or "the default printer"
    if not click.confirm(f"\nPrint to {destination}?", default=False):
        runlog.record({"outcome": "cancelled", "documents": len(built)})
        click.echo("Not printed. Mail untouched.")
        return

    uids = [item.document.origin.uid for item in built if item.document.origin.uid]
    runlog.record(
        {
            "outcome": "printing",
            "documents": [item.document.origin.identifier for item in built],
            "uids": uids,
        }
    )

    try:
        job = spool(sheets_pdf, config)
    except PrintError as error:
        runlog.record({"outcome": "print-failed", "error": str(error)})
        raise click.ClickException(
            f"{error}\nMail untouched; PDF kept at {sheets_pdf}"
        ) from error

    click.echo(f"Spooled as {job}.")
    runlog.record(
        {
            "outcome": "printed-kept" if no_retire else "printed",
            "job": job,
            "documents": [item.document.origin.identifier for item in built],
            "uids": uids,
        }
    )

    if no_retire:
        click.echo(
            "Mail untouched: messages remain starred and will be reprinted "
            "on the next run."
        )
        return

    if trash and uids:
        result = retire_printed(config, uids, trash)
        click.echo(f"Retired {len(result.retired)} message(s) to {trash}.")
