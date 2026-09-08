"""The Friday afternoon command.

The order of operations enforces the design's one invariant: mail is modified
only after a job has reached the print queue, and only for the messages whose
content actually reached it.
"""

import imaplib
import subprocess
import tempfile
import textwrap
from datetime import UTC, datetime
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
from .contents import build_contents
from .extract import extract
from .impose import impose
from .mail import Mailbox, MailError, RetireResult, password_for
from .models import Document, Verdict
from .pipeline import Built, Failure, TeaserSkippedError, build_one
from .printer import PrintError, spool
from .stamp import stamp_packet
from .summarize import build_summary_pages


def fetch_queue(config: Config) -> tuple[list[Document], str | None]:
    """Fetch the starred messages, read-only, and resolve the Trash folder.

    config.mail.trash lets a user name the Trash folder literally, for a
    server without the SPECIAL-USE extension trash_folder() depends on to
    discover it automatically - without an escape hatch, such a server
    would abort every run. "auto", the default, keeps discovering it.
    """
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
        trash = None
        if uids:
            trash = (
                box.trash_folder() if config.mail.trash == "auto" else config.mail.trash
            )
    return documents, trash


def _open_preview(pdf: Path) -> None:
    """Best-effort: open the PDF for a look before printing.

    subprocess.run(..., check=False) only suppresses a non-zero exit code;
    it still raises OSError (FileNotFoundError in practice) when the
    command itself does not exist, which crashed every run on any
    non-macOS host despite check=False. `open` is macOS-only; `xdg-open`
    is its Linux equivalent. If neither exists, the PDF's own path -
    already echoed above this call - is enough to open it by hand.
    """
    for opener in (["open", "-a", "Preview"], ["xdg-open"]):
        try:
            subprocess.run([*opener, str(pdf)], check=False)
            return
        except OSError:
            continue


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
    if result.unrecoverable:
        # The one state the user cannot discover on their own: the move
        # failed *and* the rescue re-star failed, so the message is read,
        # unstarred, and stuck in the source folder - gone from the
        # star-based queue with nothing visible in Thunderbird.
        click.echo(
            f"  WARNING: {len(result.unrecoverable)} message(s) need manual "
            f"attention - could not restore to the queue after a failed "
            f"move: {result.unrecoverable}",
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
@click.option(
    "--summary/--no-summary",
    "summary",
    default=None,
    help=(
        "Generate the two AI summary pages (a topic summary and Bamboo "
        "Weekly candidates). Needs an Anthropic API key and adds time to "
        "the run - one Claude API call over the whole packet's text. "
        "Overrides [summary] enabled in config.toml; with neither flag "
        "given, the config value decides."
    ),
)
def main(
    paper: str | None,
    config_path: Path,
    dry_run: bool,
    no_retire: bool,
    no_preview: bool,
    summary: bool | None,
) -> None:
    """Print this week's starred newsletters, four to a side, duplex."""
    try:
        config = load_config(config_path, paper_override=paper)
        documents, trash = fetch_queue(config)
    except (MailError, ConfigError) as error:
        raise click.ClickException(str(error)) from error

    if not documents:
        click.echo("Nothing starred in the queue.")
        return

    click.echo(f"Building {len(documents)} newsletters:")
    work_dir = Path(tempfile.mkdtemp(prefix="shabbat-print-"))
    built: list[Built] = []
    failed: list[Failure] = []
    with click.progressbar(
        documents,
        label="Building",
        item_show_func=lambda document: document.publication if document else None,
    ) as bar:
        for index, document in enumerate(bar):
            result = build_one(document, index, config, work_dir)
            if isinstance(result, Built):
                built.append(result)
            else:
                failed.append(result)

    for item in built:
        note = {
            Verdict.FILLER: "  (filler cell dropped)",
            Verdict.WIDOW: "  (squeezed to fit)",
            Verdict.FULL: "",
        }[item.verdict]
        click.echo(f"  {item.document.publication}: {item.cells} cells{note}")
        click.echo(
            f"    kept {item.document.images_kept} images, "
            f"dropped {len(item.document.images_dropped)}"
        )
        for block in item.document.blocks_dropped:
            preview = textwrap.shorten(block.text, width=70, placeholder="...")
            click.echo(f"    removed block: {preview!r}")
    for failure in failed:
        if isinstance(failure.error, TeaserSkippedError):
            # H2: visible, never silent, and specifically on stdout - a
            # user who disagrees with the threshold must be able to see
            # what was dropped and why, not have it scroll away on
            # stderr the way an ordinary build failure does.
            click.echo(
                f"  SKIPPED {failure.document.publication}: "
                f"{failure.document.title!r} ({failure.error.word_count} words, "
                f"below {failure.error.min_words})"
            )
        else:
            click.echo(
                f"  SKIPPED {failure.document.publication}: {failure.error}", err=True
            )

    skipped_teasers = [
        {
            "identifier": failure.document.origin.identifier,
            "publication": failure.document.publication,
            "title": failure.document.title,
            "words": failure.error.word_count,
        }
        for failure in failed
        if isinstance(failure.error, TeaserSkippedError)
    ]

    if not built:
        click.echo("Nothing could be built.", err=True)
        return

    packet_date = datetime.now(UTC).date()

    # The flag overrides the config; with neither --summary nor
    # --no-summary given, summary is None and the config's [summary]
    # enabled value decides. This applies identically on a dry run: a dry
    # run previews what will print, and a preview missing the summary
    # pages is not a preview of the real packet.
    summary_enabled = config.summary.enabled if summary is None else summary

    summary_pages: list[Built] = []
    if summary_enabled:
        outcome = build_summary_pages(built, config, packet_date, work_dir / "summary")
        summary_pages = list(outcome.pages)
        if outcome.reason is not None:
            # H3: degradation is not optional - no key, no network, an API
            # error, a malformed response, or a timeout must all still let
            # the packet print, just without these two pages. Reported on
            # stdout (not stderr), the same channel as every other line
            # describing what this run actually did.
            click.echo(
                f"  Summary skipped: {outcome.reason} "
                f"(after {outcome.elapsed_seconds:.1f}s)"
            )
        elif summary_pages:
            tokens = ""
            if outcome.input_tokens is not None and outcome.output_tokens is not None:
                tokens = (
                    f", {outcome.input_tokens} in / {outcome.output_tokens} out tokens"
                )
            click.echo(
                f"  Summary: {len(summary_pages)} page(s) in "
                f"{outcome.elapsed_seconds:.1f}s{tokens}"
            )
    else:
        # H4: the user's original complaint - no summary pages and no
        # indication why. Visible on every run where they are off, not
        # just the ones where they were attempted and degraded.
        click.echo(
            "  Summary: disabled (pass --summary to enable; needs an API "
            "key and adds time to the run)."
        )

    summary_cells = sum(item.cells for item in summary_pages)
    contents_built, contents_converged = build_contents(
        built,
        config,
        packet_date,
        work_dir / "contents",
        summary_cells=summary_cells,
    )
    if contents_built is not None:
        packet_built = [contents_built, *summary_pages, *built]
    else:
        packet_built = [*summary_pages, *built]
    if not contents_converged:
        click.echo(
            "  Contents: could not compute reliable starting cell numbers "
            "after 3 attempts - omitting the contents page.",
            err=True,
        )

    stamped = stamp_packet(
        packet_built,
        packet_date,
        config.printing.paper,
        config.layout,
        work_dir / "stamped",
    )

    sheets_pdf = work_dir / "sheets.pdf"
    sides = impose(stamped, config.printing.paper, sheets_pdf)
    cells = sum(item.cells for item in packet_built)
    click.echo(
        f"\n  {cells} cells - {sides} sheet sides on {config.printing.paper.name}"
    )
    click.echo(f"  {sheets_pdf}")

    if not no_preview:
        _open_preview(sheets_pdf)

    if dry_run:
        click.echo("\nDry run: nothing printed, nothing retired.")
        return

    destination = config.printing.printer or "the default printer"
    if not click.confirm(f"\nPrint to {destination}?", default=False):
        runlog.record(
            {
                "outcome": "cancelled",
                "documents": len(built),
                "skipped": skipped_teasers,
            }
        )
        click.echo("Not printed. Mail untouched.")
        return

    uids = [item.document.origin.uid for item in built if item.document.origin.uid]
    runlog.record(
        {
            "outcome": "printing",
            "documents": [item.document.origin.identifier for item in built],
            "uids": uids,
            "skipped": skipped_teasers,
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
            "skipped": skipped_teasers,
        }
    )

    if no_retire:
        click.echo(
            "Mail untouched: messages remain starred and will be reprinted "
            "on the next run."
        )
        return

    if trash and uids:
        try:
            result = retire_printed(config, uids, trash)
        except (MailError, imaplib.IMAP4.error, OSError) as error:
            # The job is already spooled: password_for(), Mailbox.__enter__(),
            # or imaplib's own readonly guard on the write-mode SELECT can
            # all still raise here, after printing has already succeeded.
            # OSError covers the second connection itself failing to open -
            # imaplib.IMAP4_SSL(host) raises a raw OSError (socket.gaierror
            # on a DNS blip, ssl.SSLError on a dropped VPN both being
            # subclasses of it), minutes after the first connection and
            # right after the printer has already accepted the job. Report
            # exactly what happened rather than let it escape as a
            # traceback that leaves the user unsure whether their mail was
            # touched.
            runlog.record({"outcome": "retire-failed", "error": str(error)})
            raise click.ClickException(
                f"Printed, but could not retire: {error}\n"
                f"Mail may be partly modified; check {trash} by hand."
            ) from error
        runlog.record(
            {
                "outcome": "retired",
                "trash": trash,
                "retired": list(result.retired),
                "failed": list(result.failed),
                "unrecoverable": list(result.unrecoverable),
            }
        )
        click.echo(f"Retired {len(result.retired)} message(s) to {trash}.")
