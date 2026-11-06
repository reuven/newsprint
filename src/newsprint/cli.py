"""The Friday afternoon command.

The order of operations enforces the design's one invariant: mail is modified
only after the whole packet has reached paper, and only for the messages whose
content actually reached it. "Reached paper" is deliberately stronger than
"reached the queue" - lp exits as soon as CUPS accepts the document, and a job
that later stops halfway is still reported as completed, so the run waits for
the printer and counts sides before it touches anything.
"""

import imaplib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import metadata, version
from pathlib import Path
from typing import Any

import click

from . import runlog
from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    PublicationNames,
    load_config,
    load_publication_names,
    rename_folder_key,
)
from .contents import build_contents
from .cupsjob import JobState
from .extract import extract
from .impose import impose
from .mail import (
    FETCH_CHUNK_SIZE,
    Mailbox,
    MailError,
    RetireResult,
    UnretireResult,
    password_for,
)
from .models import Document, Verdict
from .pdfutil import page_count
from .picker import (
    DISPLAY_LIMIT,
    build_picklist,
    build_trim_picklist,
    packet_sheets,
    window_since,
)
from .pickerui import questionary_prompt
from .pipeline import Built, Failure, TeaserSkippedError, build_one
from .printer import PrintError, await_completion, locate_printer, spool
from .progress import (
    printed_line,
    printing_line,
    queued_line,
    queueing_line,
    sheets_for,
    stall_lines,
)
from .reprint import (
    Pages,
    ReprintError,
    describe,
    pages_after_cell,
    parse_pages,
    resume_at,
)
from .setup import run_setup
from .stamp import format_packet_date, stamp_packet
from .summarize import build_summary_pages

# A full message, for the starred queue and for whatever the user picks -
# both are about to be printed. UID is asked for explicitly (RFC 3501
# already guarantees it for a UID FETCH response, but naming it removes
# any doubt about what Mailbox.fetch_many()'s parser is reading).
_PACKAGE = "newsprint"

_FULL_ITEMS = "(UID RFC822)"

# Headers only, for the unstarred picker's listing - it only needs
# publication and subject (From/List-Id and Subject; Date to order and
# display each row). BODY.PEEK[...] - PEEK specifically - fetches without
# setting \Seen, unlike a plain BODY[...]; scanning up to ~90 of the
# user's unread messages a week must never mark any of them read as a
# side effect of merely listing them.
_HEADER_ITEMS = "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-ID)])"

# The same, plus the header that says *which message this is*. Retiring
# from a record has to prove each uid is still the message the record
# names, and a server returns only the headers it was asked for - so
# without MESSAGE-ID here, extract() falls back to the sender address
# (see its own `default=address`), that matches none of the Message-IDs
# the run recorded, and a retirement reports every message as "no longer
# the one that run printed" and moves nothing at all. Which is exactly
# what it did on 2026-11-06, against the 25 messages this was written to
# rescue: the fake Mailbox in the tests returns whole messages whatever
# the fetch asks for, so nothing caught it until it was run for real.
_CHECK_ITEMS = "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-ID MESSAGE-ID)])"


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
    return [
        extract(raw[uid], uid=uid, names=names, folder=box.folder)
        for uid in uids
        if uid in raw
    ]


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
        "  Choose newsletters to add (type to filter, esc to clear the "
        "filter, space to toggle, enter to confirm; ctrl-c to cancel)."
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


def _offer_trim(
    built: list[Built], config: Config, summary_cells: int
) -> list[Built] | None:
    """Say what the packet costs, and above a threshold let it be cut.

    Sheets, not cells or sides: paper is what a reader carries, and the
    run that prompted this came to fifty of them. Past
    [print] trim_above_sheets the same checklist the unstarred
    newsletters use is offered, each row labelled with what it costs, so
    the reader can see where the paper went and drop what they will not
    read.

    Only with a terminal, and only above the threshold. A small packet is
    never interrupted - the editor is for the week that got away, not for
    every week - and with 0 configured nothing is ever asked.
    """
    cells = summary_cells + sum(item.cells for item in built)
    sheets = packet_sheets(cells, config.printing.paper)

    def plural(count: int, noun: str) -> str:
        return f"{count} {noun}{'' if count == 1 else 's'}"

    # Deliberately not "cells - ": the packet total printed at the end of
    # a run uses that, and a test reads the output by looking for it.
    click.echo(
        f"\n  {plural(len(built), 'newsletter')}, {plural(cells, 'cell')}, "
        f"about {plural(sheets, 'sheet')} of paper."
    )
    limit = config.printing.trim_above_sheets
    if limit <= 0 or sheets <= limit or not _stdin_is_tty():
        return built

    click.echo(f"  That is over the {limit}-sheet mark.")
    picklist = build_trim_picklist(
        [(item.document, item.cells) for item in built],
        order=config.printing.trim_order,
        today=None,
    )
    chosen = questionary_prompt(picklist, preselected=True)
    if chosen is None:
        return None
    keep = {id(document) for document in chosen}
    trimmed = [item for item in built if id(item.document) in keep]
    dropped = len(built) - len(trimmed)
    if dropped:
        remaining = packet_sheets(
            summary_cells + sum(item.cells for item in trimmed),
            config.printing.paper,
        )
        click.echo(f"  Dropped {dropped}; about {plural(remaining, 'sheet')} now.")
    return trimmed


def show_publications(config: Config) -> None:
    """List the publications in the folder, as newsprint names them.

    Writing an [images] rule means naming a publication, and the names
    are the senders' own: a real archive holds 102 distinct ones, up to
    65 characters, a few of them "b98e2de85f03865f1d38de74fmc list".
    Nobody types those from memory, so the tool says what it sees and the
    reader copies any part of one.

    Headers only, and read-only - the same scan the unstarred picker
    already makes, for the same reason: the whole bodies are not needed
    to answer this and would cost a great deal more.
    """
    config.require_mail()
    click.echo(f"Connecting to {config.mail.host} as {config.mail.user}...")
    password = password_for(config.mail.host, config.mail.user)
    names = load_publication_names()
    counts: Counter[str] = Counter()
    for folder in config.mail.folders:
        with Mailbox(
            host=config.mail.host,
            user=config.mail.user,
            password=password,
            folder=folder,
        ) as box:
            uids = box.search_all()
            click.echo(f"  {box.folder}: {len(uids)} message(s).")
            for document in _fetch_documents(
                box, uids, names, items=_HEADER_ITEMS, label="  Scanning"
            ):
                counts[document.publication] += 1
    if not counts:
        click.echo("\nNo messages, so no publications to name.")
        return
    width = max(len(name) for name in counts)
    click.echo(f"\n{len(counts)} publication(s):")
    for name, count in sorted(counts.items()):
        click.echo(f"  {name.ljust(width)}  {count}")
    click.echo(
        "\nAny part of a name works in an [images] rule, without regard "
        "to case - see config.example.toml."
    )


def fetch_queue(
    config: Config, no_pick: bool = False, since_override: date | None = None
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
    collected: list[Document] = []
    trash: str | None = None
    # One connection per folder, opened in turn. A single folder - which
    # is every config that has ever existed - is one connection, exactly
    # as before; the count only grows for someone who asked for more.
    for folder in config.mail.folders:
        with Mailbox(
            host=config.mail.host,
            user=config.mail.user,
            password=password,
            folder=folder,
            notify=lambda message: click.echo(f"  {message}"),
        ) as box:
            click.echo(f"  Opened {box.folder} ({box.message_count} messages).")
            starred_uids = box.search_flagged()
            click.echo(f"  {len(starred_uids)} starred message(s) found.")
            collected += _fetch_documents(
                box, starred_uids, names, items=_FULL_ITEMS, label="  Fetching"
            )

            picked_uids: list[int] = []
            if not no_pick:
                try:
                    today = datetime.now(UTC).date()
                    since = window_since(
                        config.fallback_days,
                        today,
                        override=since_override,
                    )
                    picked, picked_uids = _offer_picks(box, config, names, since, today)
                    collected += picked
                except MailError as error:
                    click.echo(
                        f"  Could not check for unstarred newsletters: {error}",
                        err=True,
                    )

            # The trash belongs to the account, not to a folder, so the
            # first folder able to answer settles it for the whole run.
            if trash is None:
                trash = _resolve_trash(box, config, [*starred_uids, *picked_uids])
    merged = sorted(collected, key=lambda document: document.date)
    return merged, trash


# A packet's file name, out of a title that is free text. Anything that
# is not a letter or a digit becomes a dash, which is enough to survive a
# shell, a Finder, and a mail attachment without quoting.
_FILENAME_PUNCTUATION = re.compile(r"[^a-z0-9]+")
_FILENAME_STEM_MAX = 40


def packet_filename(title: str, packet_date: date, at: datetime) -> str:
    """What the finished packet is called.

    The reader ends up holding this file - in Preview, in a Downloads
    folder, attached to a mail - so it is named for what it is rather
    than for the step that produced it.

    The time is part of it because a packet can be built twice in one
    day: a print that jams, or a second look after starring one more
    thing. Without it the second build silently replaces the first
    wherever --output points.

    `at` is read off the local clock by the caller rather than in UTC,
    unlike every other time this program handles: this one is shown to
    the reader, who compares it against the clock on their own wall.
    """
    stem = _FILENAME_PUNCTUATION.sub("-", title.casefold()).strip("-")
    if len(stem) > _FILENAME_STEM_MAX:
        # Cut on a dash so the name ends on a whole word, falling back to
        # a hard cut for a single word longer than the whole allowance.
        stem = stem[:_FILENAME_STEM_MAX].rpartition("-")[0] or stem[:_FILENAME_STEM_MAX]
    return f"{stem or _PACKAGE}-{packet_date.isoformat()}-{at:%H%M}.pdf"


def sweep_packets(directory: Path, keep_days: int, now: datetime) -> list[Path]:
    """Remove packets in `directory` older than `keep_days`, and say which.

    Only ever PDFs, and only ever directly in this directory: it is the
    one newsprint chose, but a reader may well have put something of
    their own beside the packets, and age is no reason to delete it.

    `keep_days` of 0 sweeps nothing, for anyone who would rather keep the
    lot and tidy it themselves.
    """
    if keep_days <= 0 or not directory.is_dir():
        return []
    cutoff = now.timestamp() - keep_days * 24 * 60 * 60
    swept = []
    for path in sorted(directory.glob("*.pdf")):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError:
            # A packet that cannot be read or removed is not worth
            # failing a print run over; it will be offered again next
            # time, and the run has newsletters to get to.
            continue
        swept.append(path)
    return swept


def save_output(
    built_pdf: Path, output: Path | None, filename: str, default_dir: Path
) -> Path:
    """Where the finished packet ends up, and its path afterwards.

    With no --output the packet goes to the configured directory rather
    than staying in the run's temp directory, which the OS eventually
    deletes - and a print that comes out wrong is only discovered after
    the mail has been retired, so the PDF has to outlive the run.

    An --output naming an existing directory gets a dated file inside it,
    since "where do I put this week's packet" is the common case and
    naming it by hand every week is not. Anything else is taken as the
    file path to write.
    """
    if output is None:
        default_dir.mkdir(parents=True, exist_ok=True)
        destination = default_dir / filename
        shutil.copyfile(built_pdf, destination)
        return destination
    destination = output / filename if output.is_dir() else output
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built_pdf, destination)
    return destination


def _offer_folder_rename(config_path: Path) -> None:
    """Offer to rename a deprecated `folder` key to `folders`.

    load_config warns about it every run, which on its own is a chore
    rather than a fix: it tells the reader to go and edit a file, every
    week, until they do. Doing it for them takes one keystroke.

    Only ever with a terminal to ask at. A scheduled run has nobody to
    answer, and must neither sit waiting for one nor quietly edit a file
    on its own.
    """
    if not _stdin_is_tty() or not config_path.exists():
        return
    try:
        mail = tomllib.loads(config_path.read_text()).get("mail", {})
    except (OSError, tomllib.TOMLDecodeError):
        # A config that cannot be read or parsed is load_config's problem
        # to report properly, a moment from now. Not this function's.
        return
    if "folders" in mail or "folder" not in mail:
        return
    if not click.confirm("  Rename it to folders now?", default=True):
        return
    if rename_folder_key(config_path):
        click.echo("  Renamed. Nothing else in the file was touched.")


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


@dataclass(frozen=True, slots=True)
class Packet:
    """A packet at the printer, and the mail that is waiting on it.

    One shape for both ways in: a fresh run builds it from what it has
    just rendered, and a reprint rebuilds it from the log entry of the
    run that stopped. Everything after the job is spooled - waiting,
    reporting a shortfall, retiring the mail - is then the same code
    either way, and none of it has to know which one it came from.

    `first_side` is where this job sits in the document it came out of,
    so a reprint of 37-72 that itself stops partway can still name a side
    of the original packet rather than a number that only means anything
    inside the range.
    """

    pdf: Path
    sides: int
    documents: tuple[str, ...] = ()
    messages: tuple[runlog.Message, ...] = ()
    trash: str | None = None
    skipped: tuple[dict[str, object], ...] = ()
    first_side: int = 1

    @property
    def uids(self) -> list[int]:
        return [message.uid for message in self.messages]

    def side(self, within: int) -> int:
        """Side `within` of this job, numbered as the document numbers it."""
        return self.first_side + within - 1

    def held(self) -> dict[str, object]:
        """The log fields that say which mail this run is still holding.

        `messages` is the one that can be retired from, since it carries
        each uid's folder. `documents` and `uids` stay beside it because
        every entry ever written has those and nothing else - including
        the ones in the author's own log, one of which is still holding
        25 messages - and because `pending_mail` reads `documents` to
        tell a run that has been settled from one that has not.
        """
        return {
            "documents": list(self.documents),
            "uids": self.uids,
            "messages": runlog.as_records(self.messages),
            "trash": self.trash,
            "packet": str(self.pdf),
            "skipped": list(self.skipped),
        }


def _wait_for_paper(
    config: Config,
    job: str,
    packet: Packet,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Block until the job stops, and refuse to go on if it fell short.

    This is the step whose absence cost a run on 2026-09-18: `lp` had
    exited cleanly, so newsprint retired 36 messages eleven seconds after
    handoff, and the printer then stopped at 67 of 107 sides. CUPS still
    called that job completed, which is why the check is against the
    impression count rather than the state.

    Falling short raises rather than asking, because the person who
    started the run may well have walked away from it - and because the
    answer that keeps the mail is the only safe one either way.

    The count that decides is the printer's own. CUPS's runs ahead of
    paper by whatever the printer holds in memory: on 2026-10-02 it said
    66 of 116 while the printer had cancelled the job at 60, and resuming
    from side 67 would have lost six pages. CUPS's number is shown, and
    labeled, as sides sent.
    """
    sides = packet.sides
    sheets_pdf = packet.pdf
    duplex = config.printing.duplex
    started = clock()
    printing = False

    def sent(state: JobState) -> None:
        click.echo(f"\r  {queueing_line(state.impressions, sides)}", nl=False)

    def printed(state: JobState) -> None:
        nonlocal printing
        if not printing:
            # Overwrite the queueing line rather than leaving a stale
            # count above a stage that has already moved on.
            click.echo(f"\r  {queued_line(sides)}")
            printing = True
        click.echo(
            f"\r  {printing_line(state.impressions, sides, duplex, clock() - started)}",
            nl=False,
        )

    def stalled(state: JobState, seconds: float) -> None:
        # The progress line above is mid-repaint, written with nl=False, so
        # close it before saying anything on lines of its own.
        click.echo()
        first, *rest = stall_lines(state.impressions, sides, seconds)
        click.echo(f"  ! {first}")
        for line in rest:
            click.echo(f"    {line}")

    try:
        printer = locate_printer(job, override=config.printing.printer_uri)
        outcome = await_completion(
            job,
            sides,
            on_progress=sent,
            document=sheets_pdf.name,
            printer=printer,
            on_printed=printed,
            on_stall=stalled,
        )
    except PrintError as error:
        runlog.record({"outcome": "print-unknown", "job": job, "error": str(error)})
        raise click.ClickException(
            f"{error}\nMail untouched; PDF kept at {sheets_pdf}"
        ) from error
    finally:
        click.echo()

    count = outcome.state.impressions
    if outcome.complete:
        if outcome.confirmed:
            click.echo(f"  {printed_line(sides, duplex)}")
        else:
            click.echo(
                f"  Sent {sides} sides; this printer cannot report how many "
                "reached paper."
            )
        return

    sheets = sheets_for(count, config.printing.duplex)
    runlog.record(
        {
            "outcome": "print-short",
            "job": job,
            "sides": sides,
            "impressions": count,
            "sent": outcome.sent,
            "sheets": sheets,
            "confirmed": outcome.confirmed,
            "printer_job": outcome.printer_job,
            "state": outcome.state.state,
            "reasons": list(outcome.state.reasons),
            **packet.held(),
        }
    )
    # Where to pick it up again, in the original document's own numbering
    # and rounded back to a sheet boundary. `count + 1` was wrong here:
    # on a duplex job it is an even side half the time, and CUPS lays out
    # an extracted range from scratch, so that advice printed every
    # remaining back as a front. See reprint.py.
    resume = packet.side(resume_at(count, duplex))
    finish = (
        f"To finish:\n  newsprint --reprint-from {sheets_pdf} "
        f"--pages {resume}-{packet.side(outcome.expected)}\n"
    )
    if outcome.confirmed:
        why = f": {', '.join(outcome.state.reasons)}" if outcome.state.reasons else ""
        what = (
            f"The printer stopped after {count} of {outcome.expected} sides "
            f"({sheets} sheets){why}. {outcome.shortfall} sides never printed.\n"
            f"{finish}"
        )
    else:
        what = (
            f"CUPS sent {count} of {outcome.expected} sides and stopped. This "
            "printer cannot report how many reached paper, so check the last "
            f"sheet before reprinting.\n{finish}"
        )
    raise click.ClickException(
        f"{what}"
        f"Mail untouched: all {len(packet.documents)} messages stay starred.\n"
        f"PDF kept at {sheets_pdf}"
    )


def retire_printed(
    config: Config, uids: list[int], trash: str, folder: str | None = None
) -> RetireResult:
    source = folder or config.mail.folder
    click.echo(f"\nRetiring {len(uids)} message(s) from {source} to {trash}...")
    password = password_for(config.mail.host, config.mail.user)
    with Mailbox(
        host=config.mail.host,
        user=config.mail.user,
        password=password,
        folder=source,
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


def retire_printed_by_folder(
    config: Config, documents: Sequence[Document], trash: str
) -> dict[str, RetireResult]:
    """Retire each message from the folder it was read out of.

    A uid is issued by a folder and means nothing outside it: two folders
    can both hold a uid 4, and they are different messages. Retiring a
    whole run against one folder would move whatever happened to share a
    number in the other - so the run is grouped by folder first, and each
    group goes back to its own.
    """
    by_folder: dict[str, list[int]] = {}
    for document in documents:
        uid = document.origin.uid
        if uid is None:
            continue
        source = document.origin.folder or config.mail.folder
        by_folder.setdefault(source, []).append(uid)
    return {
        folder: retire_printed(config, uids, trash, folder=folder)
        for folder, uids in by_folder.items()
    }


def unretire_last(config: Config) -> None:
    """Put the most recently retired messages back where they came from.

    Reads the last "retired" entry from the run log, finds those messages
    in the trash by Message-ID, moves them back and re-stars them. Bounded
    by the trash: once the mail host empties it there is nothing left to
    restore, and no amount of run log helps.
    """
    entry = runlog.last_retirement()
    if entry is None:
        raise click.ClickException(
            "No retirement in the run log, so there is nothing to undo."
        )
    # A run may have spanned several folders, and each message goes back
    # to its own. `folders` carries that; an entry written before it
    # existed - every entry on any installation older than this version,
    # including the ones already in the author's own log - names one
    # folder and a flat list, and still works.
    named = entry.get("folders")
    if isinstance(named, dict) and any(named.values()):
        restoring = {
            str(folder): [str(i) for i in ids] for folder, ids in named.items() if ids
        }
    else:
        restoring = {
            str(entry.get("folder") or config.mail.folder): [
                str(i) for i in entry.get("retired_ids") or []
            ]
        }
    message_ids = [i for ids in restoring.values() for i in ids]
    trash = str(entry.get("trash") or "")
    if not message_ids or not trash:
        raise click.ClickException(
            "The last retirement predates --unretire and did not record which "
            "messages it moved; they can only be restored by hand."
        )

    where = ", ".join(sorted(restoring))
    click.echo(
        f"Last retirement ({entry.get('at')}): "
        f"{len(message_ids)} message(s) moved to {trash}."
    )
    if not click.confirm(f"Move them back to {where} and re-star them?", default=False):
        click.echo("Nothing changed.")
        return

    password = password_for(config.mail.host, config.mail.user)
    restored: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for folder, ids in restoring.items():
        with Mailbox(
            host=config.mail.host,
            user=config.mail.user,
            password=password,
            folder=folder,
            notify=lambda message: click.echo(f"  {message}"),
        ) as box:
            outcome = box.unretire(ids, trash)
        restored += outcome.restored
        missing += outcome.missing
        failed += outcome.failed
    result = UnretireResult(
        restored=tuple(restored), missing=tuple(missing), failed=tuple(failed)
    )

    click.echo(f"Restored {len(result.restored)} message(s) to {where}.")
    if result.missing:
        click.echo(
            f"  {len(result.missing)} not found in {trash} - most likely the "
            "host has emptied it since.",
            err=True,
        )
    if result.failed:
        click.echo(f"  {len(result.failed)} could not be moved back.", err=True)
    click.echo("They are starred again, but still marked read.")
    runlog.record(
        {
            "outcome": "unretired",
            "folder": next(iter(restoring)),
            "folders": restoring,
            "trash": trash,
            "restored": list(result.restored),
            "missing": list(result.missing),
            "failed": list(result.failed),
        }
    )


def _mail_held_for(pdf: Path) -> tuple[dict[str, Any], bool] | None:
    """The run still holding mail for `pdf`, and whether it named it.

    A record written by this version names the packet it built, so a
    match is exact. Every record written before it names nothing at all,
    so the best answer available is the newest run whose mail is still
    waiting - which is a guess, and is reported as a guess rather than
    acted on, because the thing at stake is somebody's mail.
    """
    waiting = runlog.pending_mail()
    named = [
        entry
        for entry in waiting
        if Path(str(entry.get("packet", ""))).name == pdf.name
    ]
    if named:
        return named[0], True
    if waiting:
        return waiting[0], False
    return None


def _held_count(entry: dict[str, Any]) -> int:
    """How many messages a record is holding.

    The uids, not the Message-IDs: two messages in a packet can share an
    identifier (a mailing list that reuses one, or a message with no
    Message-ID at all, where extract.py falls back to the sender), and
    what is about to be retired is a list of uids.
    """
    return len(runlog.messages_in(entry)) or len(entry.get("uids") or [])


def _recorded_mail(
    config: Config, entry: dict[str, Any]
) -> tuple[list[runlog.Message], str]:
    """The mail a record holds as uids that know their folder, or why not.

    An older record carries a flat uid list with no folder beside it. One
    configured folder is the only case where that is not a guess: with
    two, uid 4 could have come from either, and they are two different
    messages. So that case is refused rather than resolved - the mail
    stays starred, and the next ordinary run collects it as it always has.
    """
    messages = runlog.messages_in(entry)
    if messages:
        return messages, ""
    # Non-empty by the time anything asks: pending_mail only returns runs
    # that recorded uids, and this is only ever asked about one of those.
    uids = [int(uid) for uid in entry.get("uids") or []]
    if len(config.mail.folders) > 1:
        return [], (
            "that run was recorded before newsprint kept each uid's folder "
            "beside it, and this config reads from several folders - uid "
            f"{uids[0]} could have come from any of them, and they would be "
            "different messages. The mail stays starred; the next ordinary "
            "run will collect it."
        )
    return [
        runlog.Message(identifier="", uid=uid, folder=config.mail.folders[0])
        for uid in uids
    ], ""


def retire_recorded(config: Config, entry: dict[str, Any]) -> None:
    """Retire the mail a stopped run recorded, having checked it is still
    the same mail.

    A uid is a number issued by a folder, and a record outlives the run
    that wrote it - the author's own log holds four runs whose entries
    will say "printing" forever. So the numbers are not taken on trust:
    each folder's uids are fetched headers-first, and only those whose
    Message-ID is one that run actually printed are retired. Anything
    else is reported and left exactly where it is.

    Two connections per folder, deliberately: a read-only one to check,
    then retire_printed's own. See mail.py on why the retirement
    connection stays separate from any fetch.
    """
    messages, refusal = _recorded_mail(config, entry)
    if refusal:
        click.echo(f"  Not retiring: {refusal}", err=True)
        return
    printed = {str(identifier) for identifier in entry.get("documents") or []}
    trash = str(entry.get("trash") or "")
    names = load_publication_names()
    password = password_for(config.mail.host, config.mail.user)
    retired_ids: dict[str, list[str]] = {}
    retired: list[int] = []
    failed: list[int] = []
    unrecoverable: list[int] = []
    for folder, uids in runlog.by_folder(messages).items():
        with Mailbox(
            host=config.mail.host,
            user=config.mail.user,
            password=password,
            folder=folder,
            notify=lambda message: click.echo(f"  {message}"),
        ) as box:
            found = {
                document.origin.uid: document.origin.identifier
                for document in _fetch_documents(
                    box, uids, names, items=_CHECK_ITEMS, label="  Checking"
                )
            }
            trash = trash or _resolve_trash(box, config, uids) or ""
        matching = [uid for uid in uids if found.get(uid) in printed]
        if len(matching) < len(uids):
            click.echo(
                f"  {len(uids) - len(matching)} message(s) in {folder} are no "
                "longer the ones that run printed, so they stay where they "
                "are.",
                err=True,
            )
        if not matching:
            continue
        result = retire_printed(config, matching, trash, folder=folder)
        done = set(result.retired)
        retired_ids[folder] = [found[uid] for uid in matching if uid in done]
        retired += result.retired
        failed += result.failed
        unrecoverable += result.unrecoverable

    if not retired:
        # Deliberately not recorded as a retirement, because it was not
        # one: --unretire undoes the newest "retired" entry in the log,
        # and an empty one written here would hide the real retirement it
        # was about to undo.
        runlog.record(
            {
                "outcome": "retire-none",
                "from_run": str(entry.get("at")),
                "trash": trash,
                "failed": failed,
                "unrecoverable": unrecoverable,
            }
        )
        click.echo(
            "Retired 0 message(s). Nothing was moved, so the mail stays "
            "exactly where it is."
        )
        return

    # The same shape an ordinary run writes, so --unretire can undo this
    # one too: it reads `folders` for the Message-IDs to find in the trash.
    runlog.record(
        {
            "outcome": "retired",
            "folder": next(iter(retired_ids), config.mail.folders[0]),
            "folders": retired_ids,
            "trash": trash,
            "retired_ids": [
                identifier
                for identifiers in retired_ids.values()
                for identifier in identifiers
            ],
            "retired": retired,
            "failed": failed,
            "unrecoverable": unrecoverable,
            "from_run": str(entry.get("at")),
        }
    )
    click.echo(f"Retired {len(retired)} message(s) to {trash}.")


def _offer_retirement(config: Config, held: tuple[dict[str, Any], bool] | None) -> None:
    """Ask about the mail a stopped run recorded, and retire it on a yes.

    Asked rather than assumed, even after a reprint that completed,
    because which run this packet belongs to is read out of the log and
    can be a best guess - see _mail_held_for. The plan printed above says
    which run and how many messages, so the question has its answer in
    front of it.
    """
    if held is None:
        return
    if not click.confirm(
        f"\nRetire the {_held_count(held[0])} message(s) that run was holding?",
        default=False,
    ):
        click.echo("Mail untouched.")
        return
    retire_recorded(config, held[0])


def _pages_to_reprint(
    config: Config, total: int, pages_text: str, after_cell: int | None
) -> Pages:
    """Which sides to print again, from whichever way the user said it."""
    duplex = config.printing.duplex
    if after_cell is not None:
        return pages_after_cell(
            after_cell, config.printing.paper.cells_per_side, total, duplex
        )
    if pages_text:
        return parse_pages(pages_text, total, duplex)
    return Pages(first=1, last=total)


def reprint_packet(
    config: Config,
    pdf: Path,
    pages_text: str,
    after_cell: int | None,
    *,
    dry_run: bool = False,
    no_print: bool = False,
    no_retire: bool = False,
    no_wait: bool = False,
) -> None:
    """Print part of a packet again, and retire the mail it was holding.

    The order is the invariant the whole tool turns on, unchanged: paper
    first, mail afterwards, and only once the printer has said every side
    came out. What is different is where the mail comes from - a run that
    has already ended wrote it down, so this reads the log instead of
    IMAP, and proves each uid is still the message that run printed
    before touching it.

    This exists because of 2026-11-05. The tray ran dry at side 37 of 72,
    the printer threw the job away, and finishing the packet took reading
    a cell number off the paper, working out that a duplex reprint has to
    start on an odd side, an `lp -P 37-72` by hand, and then unstarring
    25 messages one at a time.
    """
    if not pdf.is_file():
        raise click.ClickException(f"No packet to reprint at {pdf}")
    total = page_count(pdf)
    try:
        pages = _pages_to_reprint(config, total, pages_text, after_cell)
    except ReprintError as error:
        raise click.ClickException(str(error)) from error

    if no_print:
        click.echo(f"Not printing {pdf.name} - --no-print names the mail only.")
    else:
        click.echo(f"Reprinting {pdf.name}")
        click.echo(f"  {describe(pages, total, config.printing.duplex)}")
        if after_cell is not None and pages.first <= after_cell // (
            config.printing.paper.cells_per_side
        ):
            click.echo(
                f"  Cell {after_cell} left a sheet part way through, so that "
                "sheet prints again - a duplex job cannot start on a back."
            )

    held = _mail_held_for(pdf)
    if held is None:
        click.echo("  Mail: no run in the log is still holding any, so none is due.")
    elif held[1]:
        click.echo(
            f"  Mail: the run of {held[0].get('at')} is still holding "
            f"{_held_count(held[0])} message(s)."
        )
    else:
        was = Path(str(held[0].get("packet", ""))).name or "not recorded"
        click.echo(
            f"  Mail: no record names this packet, so this is the newest run "
            f"still holding any - {held[0].get('at')}, "
            f"{_held_count(held[0])} message(s), packet {was}."
        )

    if dry_run:
        click.echo("\nDry run: nothing printed, nothing retired.")
        return

    if no_print:
        # The 2026-11-05 ending: the packet was finished by hand, so the
        # only thing left undone is the mail. --no-print already means
        # "the paper came out somewhere else" on an ordinary run.
        if no_retire:
            click.echo("\nNothing to do: --no-print and --no-retire together.")
            return
        _offer_retirement(config, held)
        return

    destination = config.printing.printer or "the default printer"
    if not click.confirm(f"\nPrint sides {pages} to {destination}?", default=False):
        runlog.record(
            {"outcome": "cancelled", "reason": "reprint declined", "packet": str(pdf)}
        )
        click.echo("Not printed. Mail untouched.")
        return

    packet = Packet(
        pdf=pdf,
        sides=pages.count,
        first_side=pages.first,
        documents=(
            ()
            if held is None
            else tuple(str(i) for i in held[0].get("documents") or [])
        ),
        messages=() if held is None else tuple(runlog.messages_in(held[0])),
        trash=None if held is None else str(held[0].get("trash") or "") or None,
    )
    started: dict[str, object] = {
        "outcome": "reprinting",
        "pages": str(pages),
        **packet.held(),
    }
    if held is not None:
        started["source"] = str(held[0].get("at"))
    runlog.record(started)

    try:
        job = spool(pdf, config, pages=str(pages))
    except PrintError as error:
        runlog.record({"outcome": "print-failed", "error": str(error)})
        raise click.ClickException(
            f"{error}\nMail untouched; PDF kept at {pdf}"
        ) from error
    click.echo(f"Spooled as {job}.")

    if not no_wait:
        _wait_for_paper(config, job, packet)

    runlog.record(
        {
            "outcome": "reprinted",
            "job": job,
            "pages": str(pages),
            "waited": not no_wait,
            **packet.held(),
        }
    )

    if no_retire:
        click.echo("Mail untouched: the messages stay starred.")
        return
    _offer_retirement(config, held)


def about() -> str:
    """Three lines: what this is, where it lives, who wrote it.

    Read from the installed package metadata rather than repeated in the
    source, so it cannot drift from pyproject.toml - which is exactly how
    the README came to claim a Python version the project had already
    stopped requiring.
    """
    return "\n".join(
        (
            f"{_PACKAGE} {version(_PACKAGE)}",
            f"https://pypi.org/project/{_PACKAGE}/",
            str(metadata(_PACKAGE)["Author-email"]),
        )
    )


# The leading \b is click's marker for "do not rewrap the paragraph that
# follows"; without it the three lines are reflowed into one.
@click.command(epilog="\b\n" + about())
@click.version_option(version=version(_PACKAGE), message=about())
@click.option(
    "--paper",
    type=click.Choice(["a4", "letter"], case_sensitive=False),
    default=None,
    help="Paper size. Defaults to A4; use letter when printing in the US.",
)
@click.option(
    "--cells-per-side",
    type=click.Choice(["2", "4"]),
    default=None,
    help=(
        "How many newsletters go on each side of a sheet. Four is the "
        "default, printed portrait. Two turns the sheet landscape and "
        "puts them side by side, each a cell twice the size on the same "
        "paper, and sets the type at 12pt rather than 9pt to spend that "
        "on larger type instead of longer lines. Overrides [print] "
        "cells_per_side."
    ),
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
@click.option(
    "--no-wait",
    "no_wait",
    is_flag=True,
    help=(
        "Do not wait for the printer. newsprint normally blocks until the "
        "job finishes and only retires the mail if every side printed; this "
        "returns as soon as the queue accepts the document, which means a "
        "job that stops partway will still retire the mail."
    ),
)
@click.option(
    "--reprint-from",
    "reprint_from",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Print part of a packet again instead of building a new one, for "
        "finishing a job that stopped partway. Say which sides with --pages "
        "or --after-cell; with neither, the whole packet prints again. Once "
        "it completes, newsprint offers to retire the mail the stopped run "
        "was holding. With --no-print it prints nothing and only offers the "
        "mail, for a packet already finished by hand."
    ),
)
@click.option(
    "--pages",
    "pages_text",
    default="",
    help=(
        "Which sides to reprint: 37-72, or 37- for everything from side 37 "
        "on, or 37 for a single side. A duplex reprint has to start on an "
        "odd side - CUPS lays the range out from scratch, so starting on the "
        "back of a sheet prints every back as a front. Needs --reprint-from."
    ),
)
@click.option(
    "--after-cell",
    "after_cell",
    type=int,
    default=None,
    help=(
        "The last cell number that came out whole, instead of --pages. Cell "
        "numbers are printed on the paper, so this is the one number you can "
        "read off the stack; newsprint turns it into sides and rounds back "
        "to a whole sheet. Needs --reprint-from."
    ),
)
@click.option(
    "--setup",
    "setup_mode",
    is_flag=True,
    help=(
        "Ask what is needed and write a config file, checking the "
        "credentials and listing your folders as it goes."
    ),
)
@click.option(
    "--unretire",
    is_flag=True,
    help=(
        "Undo the last retirement: move those messages back out of the "
        "trash and re-star them. Builds nothing and prints nothing."
    ),
)
@click.option(
    "--publications",
    "list_publications",
    is_flag=True,
    help=(
        "List the publications in your folder, as newsprint names them, "
        "with how many messages each has. Use these to write [images] "
        "rules - any part of a name will do. Builds nothing and prints "
        "nothing."
    ),
)
@click.option("--no-preview", is_flag=True, help="Skip opening the PDF in Preview.")
@click.option(
    "--no-pick",
    is_flag=True,
    help=("Skip the prompt to add this week's unstarred newsletters, for a fast run."),
)
@click.option(
    "--since",
    "since_override",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help=(
        "The first day the unstarred review should include, as YYYY-MM-DD. "
        "Without it the window opens at your last successful run, or "
        "[window] fallback_days back if there has not been one. An earlier "
        "date reaches further back than that; a later one narrows it to "
        "just the last day or two. Does not affect starred messages, which "
        "are always collected however old they are."
    ),
)
@click.option(
    "--summary/--no-summary",
    "summary",
    default=None,
    help=(
        "Generate the AI summary pages (what recurs across this week's "
        "reading, and optionally what to follow up - see [summary] in "
        "config.toml). Needs an Anthropic API key and adds time to "
        "the run - one Claude API call over the whole packet's text. "
        "Overrides [summary] enabled in config.toml; with neither flag "
        "given, the config value decides."
    ),
)
def main(
    paper: str | None,
    cells_per_side: str | None,
    config_path: Path,
    dry_run: bool,
    no_retire: bool,
    output: Path | None,
    no_print: bool,
    no_wait: bool,
    reprint_from: Path | None,
    pages_text: str,
    after_cell: int | None,
    setup_mode: bool,
    unretire: bool,
    list_publications: bool,
    no_preview: bool,
    no_pick: bool,
    since_override: datetime | None,
    summary: bool | None,
) -> None:
    """Print this week's starred newsletters, four to a side, duplex."""
    if reprint_from is None and (pages_text or after_cell is not None):
        raise click.ClickException(
            "--pages and --after-cell say which part of a packet to print\n"
            "again, so they need --reprint-from to say which packet."
        )
    if pages_text and after_cell is not None:
        raise click.ClickException(
            "--pages and --after-cell both say where to start printing.\n"
            "Pass one or the other."
        )
    if setup_mode:
        run_setup(config_path)
        return
    click.echo(f"Reading config: {config_path}")
    _offer_folder_rename(config_path)
    if unretire:
        try:
            config = load_config(
                config_path,
                paper_override=paper,
                cells_override=int(cells_per_side) if cells_per_side else None,
            )
            unretire_last(config)
        except (MailError, ConfigError) as error:
            raise click.ClickException(str(error)) from error
        return
    if reprint_from is not None:
        try:
            config = load_config(
                config_path,
                paper_override=paper,
                cells_override=int(cells_per_side) if cells_per_side else None,
            )
            reprint_packet(
                config,
                reprint_from,
                pages_text,
                after_cell,
                dry_run=dry_run,
                no_print=no_print,
                no_retire=no_retire,
                no_wait=no_wait,
            )
        except (MailError, ConfigError) as error:
            raise click.ClickException(str(error)) from error
        return
    if list_publications:
        try:
            config = load_config(
                config_path,
                paper_override=paper,
                cells_override=int(cells_per_side) if cells_per_side else None,
            )
            show_publications(config)
        except (MailError, ConfigError) as error:
            raise click.ClickException(str(error)) from error
        return
    try:
        config = load_config(
            config_path,
            paper_override=paper,
            cells_override=int(cells_per_side) if cells_per_side else None,
        )
        documents, trash = fetch_queue(
            config, no_pick, since_override.date() if since_override else None
        )
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

    summary_cells = sum(item.cells for item in summary_pages)
    kept = _offer_trim(built, config, summary_cells)
    if kept is None:
        click.echo("\nCancelled; nothing printed.")
        runlog.record({"outcome": "cancelled", "reason": "cancelled at the editor"})
        return
    built = kept
    if not built:
        click.echo("\nNothing left to print.")
        runlog.record({"outcome": "cancelled", "reason": "trimmed to nothing"})
        return

    # Contents, stamping, and imposing were the one stretch left silent
    # after the fetch and build bars were added - measured at up to several
    # seconds with nothing printed, none of it broken into a per-item loop
    # a progress bar could wrap. Each step announces itself instead, so the
    # packet total never appears out of a multi-second silence.
    click.echo("\n  Building the contents page...")
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
    filename = packet_filename(
        config.packet.title, packet_date, datetime.now().astimezone()
    )
    sheets_pdf = work_dir / filename
    sides = impose(stamped, config.printing.paper, sheets_pdf)
    sheets_pdf = save_output(sheets_pdf, output, filename, config.output.directory)
    if output is None:
        swept = sweep_packets(
            config.output.directory,
            config.output.keep_days,
            datetime.now().astimezone(),
        )
        if swept:
            click.echo(
                f"  Swept {len(swept)} packet(s) older than "
                f"{config.output.keep_days} days."
            )
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

    # The folder travels with every uid from here on. A uid is issued by
    # a folder and means nothing outside it, so a record that holds only
    # numbers cannot be retired from afterwards - which is exactly what
    # stranded 25 messages on 2026-11-05. config.mail.folders[0] is the
    # fallback rather than config.mail.folder: that one still holds its
    # structural default for any config written with `folders`, so it can
    # name a folder the account does not have.
    packet = Packet(
        pdf=sheets_pdf,
        sides=sides,
        documents=tuple(item.document.origin.identifier for item in built),
        messages=tuple(
            runlog.Message(
                identifier=item.document.origin.identifier,
                uid=item.document.origin.uid,
                folder=item.document.origin.folder or config.mail.folders[0],
            )
            for item in built
            if item.document.origin.uid is not None
        ),
        trash=trash,
        skipped=tuple(skipped_teasers),
    )
    uids = packet.uids

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
        runlog.record({"outcome": "printed-elsewhere", **packet.held()})
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

        runlog.record({"outcome": "printing", **packet.held()})

        try:
            job = spool(sheets_pdf, config)
        except PrintError as error:
            runlog.record({"outcome": "print-failed", "error": str(error)})
            raise click.ClickException(
                f"{error}\nMail untouched; PDF kept at {sheets_pdf}"
            ) from error

        click.echo(f"Spooled as {job}.")

        if not no_wait:
            _wait_for_paper(config, job, packet)

        runlog.record(
            {
                "outcome": "printed-kept" if no_retire else "printed",
                "job": job,
                "sides": sides,
                "waited": not no_wait,
                **packet.held(),
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
            by_folder = retire_printed_by_folder(
                config, [item.document for item in built], trash
            )
        except (MailError, imaplib.IMAP4.error, OSError) as error:
            runlog.record({"outcome": "retire-failed", "error": str(error)})
            raise click.ClickException(
                f"Printed, but could not retire: {error}\n"
                f"Mail may be partly modified; check {trash} by hand."
            ) from error

        # One entry for the whole run, whatever it spanned: --unretire
        # undoes a run rather than a folder. `folders` says which
        # messages came from where, and the flat `retired_ids` stays
        # beside it because every entry written before today has that and
        # nothing else - including the ones in the user's own log.
        by_folder_ids: dict[str, list[str]] = {}
        retired_total: list[int] = []
        failed_total: list[int] = []
        unrecoverable_total: list[int] = []
        for folder, retirement in by_folder.items():
            done = set(retirement.retired)
            by_folder_ids[folder] = [
                item.document.origin.identifier
                for item in built
                if item.document.origin.uid in done
                and (item.document.origin.folder or config.mail.folder) == folder
            ]
            retired_total += retirement.retired
            failed_total += retirement.failed
            unrecoverable_total += retirement.unrecoverable

        runlog.record(
            {
                "outcome": "retired",
                "folder": next(iter(by_folder_ids), config.mail.folder),
                "folders": by_folder_ids,
                "trash": trash,
                "retired_ids": [
                    identifier
                    for identifiers in by_folder_ids.values()
                    for identifier in identifiers
                ],
                "retired": retired_total,
                "failed": failed_total,
                "unrecoverable": unrecoverable_total,
            }
        )
        retirement = RetireResult(
            retired=tuple(retired_total),
            failed=tuple(failed_total),
            unrecoverable=tuple(unrecoverable_total),
        )
        click.echo(f"Retired {len(retirement.retired)} message(s) to {trash}.")
