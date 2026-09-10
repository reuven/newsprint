"""The Friday afternoon command.

The order of operations enforces the design's one invariant: mail is modified
only after a job has reached the print queue, and only for the messages whose
content actually reached it.
"""

import imaplib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import click

from . import runlog
from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    PublicationNames,
    load_config,
    load_publication_names,
)
from .contents import build_contents
from .extract import extract
from .impose import impose
from .mail import FETCH_CHUNK_SIZE, Mailbox, MailError, RetireResult, password_for
from .models import Document, Verdict
from .picker import DISPLAY_LIMIT, build_picklist, window_since
from .pickerui import questionary_prompt
from .pipeline import Built, Failure, TeaserSkippedError, build_one
from .printer import PrintError, spool
from .stamp import format_packet_date, stamp_packet
from .summarize import build_summary_pages

# A full message, for the starred queue and for whatever the user picks -
# both are about to be printed. UID is asked for explicitly (RFC 3501
# already guarantees it for a UID FETCH response, but naming it removes
# any doubt about what Mailbox.fetch_many()'s parser is reading).
_FULL_ITEMS = "(UID RFC822)"

# Headers only, for the unstarred picker's listing - it only needs
# publication and subject (From/List-Id and Subject; Date to order and
# display each row). BODY.PEEK[...] - PEEK specifically - fetches without
# setting \Seen, unlike a plain BODY[...]; scanning up to ~90 of the
# user's unread messages a week must never mark any of them read as a
# side effect of merely listing them.
_HEADER_ITEMS = "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-ID)])"


def _fetch_documents(
    box: Mailbox, uids: list[int], names: PublicationNames, items: str, label: str
) -> list[Document]:
    """Fetch `uids` in as few IMAP round trips as possible, and report how
    long it took.

    Measured against the live queue, a batched UID FETCH is a 40x speed-up
    over fetching one message at a time (6.95s -> 0.17s for 40 headers) -
    the cost was per-command latency, not bandwidth, so batching is the
    fix, not fetching less. A batched fetch is one server round trip
    regardless of how many uids it covers, so a per-message progress bar
    no longer means anything; below FETCH_CHUNK_SIZE uids - every
    ordinary run (~21 starred, ~98 unstarred candidates) - this is a
    single call, reported as one line with the count and elapsed time.
    Above it, a click.progressbar tracks progress across the chunks
    Mailbox.fetch_many() itself issues one UID FETCH per (see
    FETCH_CHUNK_SIZE's own docstring for why chunking exists at all),
    hiding itself on a non-tty exactly like the build bar.
    """
    if not uids:
        return []
    started = time.monotonic()
    raw: dict[int, bytes]
    if len(uids) <= FETCH_CHUNK_SIZE:
        raw = box.fetch_many(uids, items)
    else:
        chunks = [
            uids[start : start + FETCH_CHUNK_SIZE]
            for start in range(0, len(uids), FETCH_CHUNK_SIZE)
        ]
        raw = {}
        with click.progressbar(
            chunks,
            label=label,
            item_show_func=lambda chunk: f"{len(chunk)} messages" if chunk else None,
        ) as bar:
            for chunk in bar:
                raw.update(box.fetch_many(chunk, items))
    elapsed = time.monotonic() - started
    click.echo(f"{label} {len(raw)} message(s) in {elapsed:.2f}s.")
    return [extract(raw[uid], uid=uid, names=names) for uid in uids if uid in raw]


def _resolve_trash(box: Mailbox, config: Config, uids: Sequence[int]) -> str | None:
    """Discover or use the configured Trash folder, and say which.

    config.mail.trash lets a user name the Trash folder literally, for a
    server without the SPECIAL-USE extension trash_folder() depends on to
    discover it automatically - without an escape hatch, such a server
    would abort every run. "auto", the default, keeps discovering it.
    Skipped entirely when there is nothing to retire.
    """
    if not uids:
        return None
    if config.mail.trash == "auto":
        trash = box.trash_folder()
        click.echo(f"  Discovered Trash folder: {trash}")
    else:
        trash = config.mail.trash
        click.echo(f"  Using configured Trash folder: {trash}")
    return trash


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def fetch_unstarred(
    box: Mailbox, since: date, names: PublicationNames
) -> list[Document]:
    """The unstarred review window, on the connection already opened by
    fetch_queue: only the headers needed for the listing (see
    _HEADER_ITEMS) for everything unflagged since `since`.

    Extracted with the same extract() the starred queue uses, but on
    headers-only bytes - extract() still parses publication, title, and
    date correctly from those (body/html simply comes back empty, which
    the listing never reads). Most of what this returns will never be
    picked, so fetching full content for all of it here - the previous
    behavior, and the actual cost behind the ~17s unstarred scan - would
    make a large window slow for no reason; full content for whatever the
    user actually picks is fetched separately, only for those uids, by
    fetch_picked().
    """
    uids = box.search_unflagged_since(since)
    click.echo(
        f"  {len(uids)} unstarred message(s) found since {format_packet_date(since)}."
    )
    return _fetch_documents(box, uids, names, items=_HEADER_ITEMS, label="  Scanning")


def fetch_picked(
    box: Mailbox, uids: list[int], names: PublicationNames
) -> list[Document]:
    """Full content for exactly the uids the user picked - the only
    unstarred candidates worth the cost, since most of a large window is
    never picked (see picker.py's own module docstring)."""
    return _fetch_documents(box, uids, names, items=_FULL_ITEMS, label="  Fetching")


def _offer_picks(
    box: Mailbox,
    config: Config,
    names: PublicationNames,
    since: date,
    today: date,
) -> tuple[list[Document], list[int]]:
    """Show what else arrived since the last successful run and let the
    user add some to the packet.

    Item 5 of the original request - "I sometimes want to print some of
    the week's non-starred newsletters, too." Runs before anything is
    built or rendered, so a pick joins the pipeline exactly like a
    starred document: same cleaning, rendering, trimming, contents entry,
    footer, and retirement, with no special case needed anywhere for it.

    Uses the same read-only connection fetch_queue already opened for the
    starred fetch - the tool used to open a second connection here, one
    full connect/login/select cycle just for this scan. Any problem on
    this connection's own operations (a mid-session server hiccup) is
    reported and swallowed by the caller rather than aborting the run -
    the starred queue this run was already going to build must still
    print; see fetch_queue's own docstring.
    """
    candidates = fetch_unstarred(box, since, names)
    when = format_packet_date(since)
    if not candidates:
        click.echo(f"  No unstarred newsletters since {when}.")
        return [], []

    candidate_uids = [
        document.origin.uid
        for document in candidates
        if document.origin.uid is not None
    ]
    sizes = box.fetch_sizes(candidate_uids)

    # --dry-run previews the packet, it is not a non-interactive mode - the
    # whole point of showing this list is letting the user add to it, so a
    # dry run with a real terminal on stdin must still prompt. The only
    # reason to skip it is that stdin genuinely is not a terminal (e.g.
    # piped input, or a CI run). The interactive checkbox is genuinely
    # scrollable, so only the non-interactive text fallback below still
    # needs DISPLAY_LIMIT's flood protection - see its own docstring.
    interactive = _stdin_is_tty()
    picklist = build_picklist(
        candidates,
        sizes,
        limit=None if interactive else DISPLAY_LIMIT,
        today=today,
    )
    shown = sum(len(group.rows) for group in picklist.groups)

    click.echo(f"\n  Newsletters since {when} you haven't starred:")
    if picklist.total > shown:
        click.echo(f"  {picklist.total} found; showing the most recent {shown}.")
    else:
        click.echo(f"  {picklist.total} unstarred newsletter(s) found.")

    if not interactive:
        click.echo("  Skipping the selection prompt (stdin is not a terminal).")
        for group in picklist.groups:
            click.echo(f"  {group.publication}")
            for row in group.rows:
                click.echo(f"    {row.document.title}  ({row.when})  [{row.length}]")
        return [], []

    click.echo(
        "  Choose newsletters to add (space to toggle, enter to confirm; ctrl-c to cancel)."
    )
    picked_candidates = questionary_prompt(picklist) or []
    if not picked_candidates:
        return [], []

    picked_uids = [
        document.origin.uid
        for document in picked_candidates
        if document.origin.uid is not None
    ]
    picked = fetch_picked(box, picked_uids, names)
    click.echo(f"  Added {len(picked)} newsletter(s) from the unstarred list.")
    return picked, picked_uids


def fetch_queue(
    config: Config, no_pick: bool = False
) -> tuple[list[Document], str | None]:
    """Fetch the starred queue and, unless no_pick, offer this week's
    unstarred newsletters to add - both on the SAME read-only IMAP
    connection.

    The tool used to open two separate connections here: one for the
    starred fetch, a second (inside what is now _offer_picks/
    fetch_unstarred) for the unstarred scan - each paying its own
    connect/login/select round trip. Doing both inside one
    `with Mailbox(...) as box:` removes that second connect entirely. The
    retirement connection, opened later by retire_printed() only after a
    job has reached the print queue, stays deliberately separate - see
    mail.py's own module docstring on why that one must not be collapsed
    in.

    A MailError raised specifically by the picker's own operations (the
    unstarred SEARCH or its header fetch) is caught here and swallowed,
    exactly as _offer_picks's own docstring says: the starred queue this
    run was already going to build must still print. A MailError raised
    by the starred fetch itself, or by opening the connection at all,
    is not caught - that failure is fatal to the whole run, same as
    always.

    The starred and picked documents are merged in ascending date order,
    not starred-then-picked - the starred set already arrives in that
    order (IMAP SEARCH returns uids ascending, and uid order tracks
    arrival order), so sorting the combined list is what puts a pick
    between the two starred issues it actually falls between, rather than
    clumping every pick after every starred document regardless of when
    it was sent. extract._date() always attaches UTC to a naive parse, so
    every Document.date is timezone-aware and comparable regardless of
    the sender's own offset.
    """
    config.require_mail()
    click.echo(f"Connecting to {config.mail.host} as {config.mail.user}...")
    password = password_for(config.mail.host, config.mail.user)
    names = load_publication_names()
    with Mailbox(
        host=config.mail.host,
        user=config.mail.user,
        password=password,
        folder=config.mail.folder,
        notify=lambda message: click.echo(f"  {message}"),
    ) as box:
        click.echo(f"  Opened {config.mail.folder} ({box.message_count} messages).")
        starred_uids = box.search_flagged()
        click.echo(f"  {len(starred_uids)} starred message(s) found.")
        documents = _fetch_documents(
            box, starred_uids, names, items=_FULL_ITEMS, label="  Fetching"
        )

        picked: list[Document] = []
        picked_uids: list[int] = []
        if not no_pick:
            try:
                today = datetime.now(UTC).date()
                since = window_since(config.fallback_days, today)
                picked, picked_uids = _offer_picks(box, config, names, since, today)
            except MailError as error:
                click.echo(
                    f"  Could not check for unstarred newsletters: {error}", err=True
                )

        trash = _resolve_trash(box, config, [*starred_uids, *picked_uids])
    merged = sorted([*documents, *picked], key=lambda document: document.date)
    return merged, trash


def save_output(built_pdf: Path, output: Path | None, packet_date: date) -> Path:
    """Where the finished packet ends up, and its path afterwards.

    With no --output the packet stays in the run's temp directory, which
    is right for a run that prints immediately and useless for one that
    hands the user a PDF to print themselves - a random directory under
    /var/folders that the OS eventually deletes.

    An --output naming an existing directory gets a dated file inside it,
    since "where do I put this week's packet" is the common case and
    naming it by hand every week is not. Anything else is taken as the
    file path to write.
    """
    if output is None:
        return built_pdf
    destination = (
        output / f"newsprint-{packet_date.isoformat()}.pdf"
        if output.is_dir()
        else output
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built_pdf, destination)
    return destination


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
    click.echo(f"\nRetiring {len(uids)} message(s) to {trash}...")
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
    "--dry-run",
    is_flag=True,
    help=(
        "Build and preview the PDF, but print nothing and touch no mail. "
        "Equivalent to --no-print --no-retire together."
    ),
)
@click.option(
    "--no-retire",
    is_flag=True,
    help=(
        "Print, but do not retire: unlike --dry-run, this really prints. "
        "The messages stay starred and will be reprinted on the next run."
    ),
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Write the finished PDF here instead of leaving it in a temp "
        "directory. An existing directory gets a dated file inside it."
    ),
)
@click.option(
    "--no-print",
    "no_print",
    is_flag=True,
    help=(
        "Build the PDF but do not send it to a printer - print it yourself "
        "from the PDF. Unlike --dry-run, this still offers to retire the "
        "mail, after asking whether the printing actually worked."
    ),
)
@click.option("--no-preview", is_flag=True, help="Skip opening the PDF in Preview.")
@click.option(
    "--no-pick",
    is_flag=True,
    help=("Skip the prompt to add this week's unstarred newsletters, for a fast run."),
)
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
    output: Path | None,
    no_print: bool,
    no_preview: bool,
    no_pick: bool,
    summary: bool | None,
) -> None:
    """Print this week's starred newsletters, four to a side, duplex."""
    click.echo(f"Reading config: {config_path}")
    try:
        config = load_config(config_path, paper_override=paper)
        documents, trash = fetch_queue(config, no_pick)
    except (MailError, ConfigError) as error:
        raise click.ClickException(str(error)) from error

    if not documents:
        click.echo("Nothing starred in the queue.")
        return

    click.echo(f"Building {len(documents)} newsletters:")
    work_dir = Path(tempfile.mkdtemp(prefix="newsprint-"))
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
        # A fetch failure is reported distinctly from an ordinary drop:
        # clean.py already decided this image was worth keeping - the
        # image itself just did not answer, or came back unusable, at
        # render time. Folding it into "dropped" would blur a deliberate
        # editorial choice (clean.py's own _is_argument_figure) with an
        # unrelated network hiccup.
        fetch_failed = (
            f", {len(item.image_fetch_failures)} fetch failed"
            if item.image_fetch_failures
            else ""
        )
        click.echo(
            f"    kept {item.document.images_kept} images, "
            f"dropped {len(item.document.images_dropped)}{fetch_failed}"
        )
        for url in item.image_fetch_failures:
            click.echo(f"    image fetch failed: {url}")
        for block in item.document.blocks_dropped:
            preview = textwrap.shorten(block.text, width=70, placeholder="...")
            # A duplicate-title removal is not data loss - see
            # DroppedBlock.kind's own docstring and the derive-chrome
            # spec's part E - and must not be reported the same way a
            # genuine chrome removal is, which reads as data loss.
            label = (
                "removed duplicate title"
                if block.kind == "duplicate_title"
                else "removed block"
            )
            click.echo(f"    {label}: {preview!r}")
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
        # The one multi-second stretch that used to print nothing until it
        # was already over: a single Claude API call covering every
        # newsletter in the packet, measured at 17.9s and 160k input
        # tokens on the user's own queue. Announced before the call, not
        # after, so the wait reads as expected rather than as a hang.
        click.echo(
            f"\n  Writing the summary pages from {len(built)} newsletter(s) - "
            "one Claude API call, so this takes a moment..."
        )
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

    # Contents, stamping, and imposing were the one stretch left silent
    # after the fetch and build bars were added - measured at up to several
    # seconds with nothing printed, none of it broken into a per-item loop
    # a progress bar could wrap. Each step announces itself instead, so the
    # packet total never appears out of a multi-second silence.
    click.echo("\n  Building the contents page...")
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

    click.echo(f"  Stamping {len(packet_built)} page(s)...")
    stamped = stamp_packet(
        packet_built,
        packet_date,
        config.printing.paper,
        config.layout,
        work_dir / "stamped",
    )

    click.echo("  Imposing onto sheets...")
    sheets_pdf = work_dir / "sheets.pdf"
    sides = impose(stamped, config.printing.paper, sheets_pdf)
    sheets_pdf = save_output(sheets_pdf, output, packet_date)
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

    uids = [item.document.origin.uid for item in built if item.document.origin.uid]

    if no_print:
        # Nothing was sent to a printer, so the one fact that decides
        # whether this mail is done with - did paper actually come out? -
        # is not something this process can observe. Ask, rather than
        # assume either way. --no-retire has already answered it.
        if no_retire:
            click.echo(f"\nNot printed here. Mail untouched.\n  {sheets_pdf}")
            return
        if not click.confirm(
            f"\nPrinted {sheets_pdf} yourself? Retire these messages?",
            default=False,
        ):
            runlog.record(
                {
                    "outcome": "cancelled",
                    "documents": len(built),
                    "skipped": skipped_teasers,
                }
            )
            click.echo("Mail untouched.")
            return
        runlog.record(
            {
                "outcome": "printed-elsewhere",
                "documents": [item.document.origin.identifier for item in built],
                "uids": uids,
                "skipped": skipped_teasers,
            }
        )
    else:
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
            retirement = retire_printed(config, uids, trash)
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
                "retired": list(retirement.retired),
                "failed": list(retirement.failed),
                "unrecoverable": list(retirement.unrecoverable),
            }
        )
        click.echo(f"Retired {len(retirement.retired)} message(s) to {trash}.")
