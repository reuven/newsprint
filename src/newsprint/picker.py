"""Choose additional newsletters from the week's unstarred mail.

The starred queue is always the default packet; this is the "too" in the
user's original request - "I sometimes want to print some of the week's
non-starred newsletters, too." Nothing here cleans or renders a candidate:
that only happens for whatever actually gets picked, in
pipeline.build_one(), exactly as it does for the starred queue - most of
what is shown here will never be picked, so doing that work up front would
make a large week slow for no reason.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import accumulate
from pathlib import Path
from typing import Literal

from wcwidth import wcwidth

from .mail import _IMAP_MONTHS as _MONTHS
from .models import Document
from .runlog import last_successful_run

# The user's own archive holds roughly 2,200 messages, of which a week is
# roughly 25 - but the window can grow far larger than that if a while has
# passed since the last successful run. Past this many candidates, a plain
# text listing would flood rather than inform - the limit that motivated
# this constant. The interactive checkbox prompt is genuinely scrollable
# (that is the whole point of replacing the numbered list with it), so
# cli.py passes limit=None there and reserves this cap for the
# non-interactive text fallback, where the old flooding concern still
# applies.
DISPLAY_LIMIT = 60

# Size terciles measured against the user's live 99-message unstarred
# window (~79KB / ~111KB - see tui-picker.md's Part 2), rounded for
# readability. RFC822.SIZE correlates only 0.71 with real cleaned word
# count - a photo-heavy issue can size as "long" while reading short -
# so these buckets are deliberately coarse rather than a specific word
# count or reading-time estimate, which would claim more precision than
# the measurement supports. A partial-body fetch was also measured
# (16-64KB BODY.PEEK[]<0.N>) and correlated no better (0.58-0.70) while
# costing 10-40x more than RFC822.SIZE, so it was not adopted - see
# tui-picker.md and this project's report for the numbers.
_SHORT_MAX_BYTES = 80_000
_MEDIUM_MAX_BYTES = 110_000


def length_label(size: int | None) -> str:
    """A deliberately coarse three-way length bucket for one candidate.

    None means no size was fetched for this uid (should not happen in
    practice - fetch_sizes raises on a missing uid rather than return a
    partial result - but a picker row must still render something sane
    rather than crash if it ever does).
    """
    if size is None:
        return "length unknown"
    if size < _SHORT_MAX_BYTES:
        return "short"
    if size < _MEDIUM_MAX_BYTES:
        return "medium"
    return "long"


def format_pick_date(day: date, today: date | None = None) -> str:
    """`%-d %b`, without strftime's locale-dependent `%b` and without a
    year.

    Mirrors stamp.format_packet_date's own fix for %b (a Hebrew locale
    turned it into "ספט׳" once - see mail._IMAP_MONTHS and
    stamp.format_packet_date), but this picker-only formatter drops the
    year that footer keeps: every candidate in the unstarred review
    window is from the same few weeks, so "3 Sep 2026" carries the same
    information as "3 Sep" in a third of the width - see
    picker-layout.md point 3. The footer itself (stamp.format_packet_date)
    is unaffected; a printed page still needs the year.

    `today` adds "(today)" or "(yesterday)" beside the date when the
    candidate is that recent - the two the user reads as "just arrived"
    and has to work out from the number otherwise. The date itself always
    stays, so the column still sorts and reads the same way; passing None
    (the default) keeps the bare stamp, which is what every test that
    does not care about relative dates wants. Both sides of the
    comparison are UTC dates - `day` comes from Document.date, which
    extract._date always makes timezone-aware - so a candidate never
    reads as "today" against a differently-zoned notion of now.
    """
    stamp = f"{day.day} {_MONTHS[day.month - 1]}"
    if today is None:
        return stamp
    if day == today:
        return f"{stamp} (today)"
    if day == today - timedelta(days=1):
        return f"{stamp} (yesterday)"
    return stamp


# Hosts that identify a publishing platform, not a publication: every
# Substack shares substack.com, so the bare domain says nothing a reader
# does not already know. What is informative is the label in front of it
# ("cloudirregular.substack.com"), which is the publication's own name.
_PLATFORM_HOSTS = (
    "substack.com",
    "buttondown.email",
    "ghost.io",
    "beehiiv.com",
    "campaign-archive.com",
)

# Two-part public suffixes, so a host under one is not reduced past its
# actual name ("bbc.co.uk" must not become "co.uk"). Only the ones the
# archive's senders could plausibly use; an unknown compound suffix
# degrades to showing one label more than needed, never to hiding the
# name.
_COMPOUND_SUFFIXES = frozenset(
    {"co.uk", "org.uk", "ac.uk", "co.il", "com.au", "co.nz", "co.jp", "com.br"}
)

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    return _NOT_ALPHANUMERIC.sub("", text.lower())


def _registrable(host: str) -> str:
    """`host` reduced to the domain someone registered.

    Senders route mail through per-newsletter subdomains that name the
    mail stream, not the publication - the New York Times sends DealBook
    from dk.nytimes.com and The Morning from nn.nytimes.com. "nytimes.com"
    is the part a reader recognizes; "dk." and "nn." are noise. Taking the
    last two labels gets that without a hand-maintained list of every
    prefix a sender might invent.
    """
    labels = host.split(".")
    if len(labels) > 2 and ".".join(labels[-2:]) in _COMPOUND_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _informative_host(host: str) -> str | None:
    """`host` reduced to what actually identifies the publication, or
    None if nothing does."""
    for platform in _PLATFORM_HOSTS:
        if host == platform:
            return None
        if host.endswith(f".{platform}"):
            # The label in front of the platform is the publication:
            # "cloudirregular.substack.com" -> "cloudirregular".
            return host[: -(len(platform) + 1)].rpartition(".")[2]
    return _registrable(host)


def source_label(publication: str, source_host: str | None) -> str | None:
    """What to show beside `publication`, or None when it would add
    nothing.

    Some newsletters are named after their author and nothing else - the
    picker shows "Jon Kelly" with no hint that it is Puck, or "Ben
    Thompson" with no hint that it is Stratechery. Showing the sending
    host fixes that, but showing it unconditionally is worse than not
    showing it: "Axios Macro (axios.com)" and "Derek Thompson
    (derekthompson.substack.com)" are pure noise.

    So it is shown only when it adds information the name does not
    already carry - decided by comparing the host's own name, with its
    TLD and any platform suffix removed, against the publication name.
    Either containing the other means the name already says it. This is
    deliberately not an attempt to detect whether a name is a person's:
    measured against the archive, capitalization and word-count rules
    classify "Axios Macro" and "The Economist" as people.
    """
    if not source_host:
        return None
    label = _informative_host(source_host)
    if not label:
        return None
    stem = _fold(label.rpartition(".")[0] or label)
    folded_publication = _fold(publication)
    if not stem or stem in folded_publication or folded_publication in stem:
        return None
    return label


def window_since(
    fallback_days: int, today: date, state_dir: Path | None = None
) -> date:
    """The first day the unstarred review window should include.

    `last_successful_run` already distinguishes `printed` (a genuine run)
    from `printed-kept` (a --no-retire rehearsal, which deliberately does
    not count) - see runlog.last_successful_run's own docstring. That
    means a rehearsal never advances this window, so the user does not
    lose track of what they have already been shown.
    """
    last = last_successful_run(state_dir=state_dir)
    if last is not None:
        return last.date()
    return today - timedelta(days=fallback_days)


@dataclass(frozen=True, slots=True)
class PickRow:
    """One selectable row: a candidate plus what the picker shows for it.

    `when` and `length` are pre-formatted here (rather than left for the
    presentation layer to compute from `document`) so that the thin
    questionary wrapper - the only part of this feature that is not
    directly unit-tested - has nothing left to do but hand these strings
    to the library; see cli.py's own module docstring on why that
    boundary matters for coverage.
    """

    document: Document
    when: str
    length: str


@dataclass(frozen=True, slots=True)
class PickGroup:
    """One publication's rows, oldest first - the grouping the old
    numbered list already did."""

    publication: str
    rows: tuple[PickRow, ...]
    # What to show beside the publication name, or None - see
    # source_label. Resolved here rather than at render time so the
    # heading stays a pure formatting step.
    source: str | None = None


@dataclass(frozen=True, slots=True)
class Picklist:
    """The candidates as picker groups, ready to prompt.

    `total` is the true candidate count even when `groups` has been
    capped to `limit` - so a caller can always say how many there really
    were, never silently showing a partial count without saying so.
    """

    groups: tuple[PickGroup, ...]
    total: int


def build_picklist(
    candidates: Sequence[Document],
    sizes: Mapping[int, int],
    limit: int | None = DISPLAY_LIMIT,
    today: date | None = None,
) -> Picklist:
    """Group by publication, sort each group oldest first, and label each
    row with its length (see length_label).

    `limit` caps a large window to the most recent `limit` candidates,
    exactly like the old numbered list did - pass None for no cap (see
    DISPLAY_LIMIT's own docstring on when that is the right choice).
    `sizes` maps a document's uid to its RFC822.SIZE in bytes; a uid with
    no entry (or None uid) gets length_label(None) - "length unknown" -
    rather than crashing on a lookup failure.
    """
    total = len(candidates)
    shown = list(candidates)
    if limit is not None and total > limit:
        shown = sorted(shown, key=lambda document: document.date, reverse=True)[:limit]

    grouped: dict[str, list[Document]] = {}
    for document in shown:
        grouped.setdefault(document.publication, []).append(document)
    for publication_documents in grouped.values():
        publication_documents.sort(key=lambda document: document.date)

    groups = tuple(
        PickGroup(
            publication=publication,
            rows=tuple(
                PickRow(
                    document=document,
                    when=format_pick_date(document.date.date(), today),
                    length=length_label(
                        sizes.get(document.origin.uid)
                        if document.origin.uid is not None
                        else None
                    ),
                )
                for document in grouped[publication]
            ),
            source=source_label(publication, grouped[publication][0].source_host),
        )
        for publication in sorted(grouped, key=str.casefold)
    )
    return Picklist(groups=groups, total=total)


# ---------------------------------------------------------------------------
# Rendering the picklist into terminal lines.
#
# All of this is plain-data layout logic with no questionary import - the
# thin wrapper in pickerui.py has nothing left to decide but which
# questionary object (Separator or Choice) each PickLine becomes. See
# picker-layout.md, the spec this section exists to satisfy: the old
# rendering had no blank line before a group heading (so "Axios Macro"
# read as a caption for the item above it, not a heading for the items
# below), and dates/lengths trailed each subject at ragged positions
# instead of forming columns.
# ---------------------------------------------------------------------------

# questionary's own InquirerControl reserves screen columns before it ever
# gets to our title string: a 3-column pointer gutter (" » " when the
# cursor is on a row, or 3 plain spaces otherwise - see
# InquirerControl._get_choice_tokens) plus, for a selectable Choice only,
# a 2-column checkbox indicator ("○ " / "● " - INDICATOR_UNSELECTED /
# INDICATOR_SELECTED, both single-width). A Separator gets the 3-column
# gutter but no indicator. Verified by reading questionary 2.1.1's own
# source (questionary/prompts/common.py, questionary/constants.py) rather
# than assumed - the same lesson the project already learned about "esc
# to cancel". These are consumed only here, computing how many columns
# our own text is allowed to use; the library adds them back on top.
_ROW_GUTTER = 5
_HEADING_GUTTER = 3

# Below this many columns, hold the layout at this floor rather than
# crushing every column toward zero - a pathologically narrow terminal
# renders the same as a 40-column one (wider than the real terminal, so a
# row may still wrap) rather than becoming unreadable.
_MIN_TERMINAL_WIDTH = 40
# Never truncate a subject narrower than this, even if the date/length
# columns would otherwise leave less room than that.
_MIN_SUBJECT_WIDTH = 12

_GAP = "  "
_ELLIPSIS = "…"

# A stray control character in a subject (an embedded CRLF from an
# unfolded header continuation, seen on the user's own live queue - a
# real message titled "...clearly\r\nexplained!") is not something any
# amount of column-width math can defend against: handed straight to the
# terminal, a raw \r jumps the cursor back to column 0 mid-row and the
# rest of the line overwrites/wraps regardless of how carefully it was
# padded - exactly the "wrapping into a mess" the spec's narrow-terminal
# note warns about, just triggered by dirty data instead of a narrow
# terminal. Collapsed to a single space rather than dropped outright, so
# "clearly\r\nexplained" reads as "clearly explained" and not
# "clearlyexplained".
_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")


def _sanitize_subject(text: str) -> str:
    return _CONTROL_RUN.sub(" ", text).strip()


def _display_width(text: str) -> int:
    """Terminal column width of `text`, not `len(text)`.

    A leading emoji in a subject line (kept deliberately - see
    picker-layout.md point 4) can occupy two terminal columns for one
    character; using len() there would make a subject that starts with
    one push the date/length columns half a character further right
    than a subject that doesn't, which is exactly the misalignment the
    spec calls out. wcwidth returns -1 for a non-printable control
    character - not expected in a cleaned subject line, but counted as 0
    rather than let one bad character make a width negative and corrupt
    every downstream padding/truncation sum.
    """
    return sum(max(wcwidth(ch), 0) for ch in text)


def _truncate_to_width(text: str, max_width: int) -> str:
    """Truncate `text` to at most `max_width` terminal columns, appending
    an ellipsis, snapped back to the last full word that fits - "Write
    Things Down (Stratechery A…" reads as a subject clipped for space;
    a raw mid-word cut reads as a bug. Falls back to a mid-word cut only
    when not even one whole word fits in the budget (the same rule
    contents.py's own _truncate_to_width applies to the printed contents
    page, reimplemented here against terminal columns instead of PDF
    point widths - this is a monospace terminal, so wcwidth stands in
    for pymupdf.get_text_length there).
    """
    if max_width <= 0:
        return ""
    if _display_width(text) <= max_width:
        return text
    ellipsis_width = _display_width(_ELLIPSIS)
    budget = max_width - ellipsis_width
    if budget <= 0:
        # max_width > 0 (checked above) and budget <= 0 together force
        # max_width == ellipsis_width == 1 - not even one real character
        # fits next to the ellipsis, so the ellipsis alone is the whole
        # answer.
        return _ELLIPSIS
    # Cumulative display width per prefix length, character by character.
    # A plain (break-free) loop, not a for/break: text's full display
    # width is already known to exceed max_width > budget (the two
    # checks above), so a hand-rolled "for ch in text: ... break" here
    # could never actually complete without breaking - an unreachable
    # branch no test could ever exercise honestly. Counting how many
    # cumulative widths stay within budget gets the same "how much fits"
    # answer without that dead branch.
    cumulative = accumulate(max(wcwidth(ch), 0) for ch in text)
    cutoff = sum(1 for width in cumulative if width <= budget)
    trimmed = text[:cutoff].rstrip()
    if " " in trimmed:
        word_boundary = trimmed.rsplit(" ", 1)[0].rstrip()
        if word_boundary:
            return f"{word_boundary}{_ELLIPSIS}"
    return f"{trimmed}{_ELLIPSIS}" if trimmed else _ELLIPSIS


def _heading_rule(publication: str, source: str | None, width: int) -> str:
    """`── PUBLICATION ──────...`, filled to `width` terminal columns.

    Capitalized, with rule characters on both sides - unmistakably a
    heading rather than a caption trailing the row above it, and legible
    without relying on color as the only cue (some users' terminal
    themes make a dim color hard to see - see picker-layout.md point 2).
    """
    # The publication is upper-cased, the source is not: a host read as
    # "PUCK.NEWS" looks like shouting rather than an address.
    named = (
        publication.upper() if source is None else f"{publication.upper()} ({source})"
    )
    label = f"── {named} "
    label_width = _display_width(label)
    if label_width >= width:
        return _truncate_to_width(label.rstrip(), width)
    return label + "─" * (width - label_width)


def _column_widths(rows: Sequence[PickRow], width: int) -> tuple[int, int, int]:
    """(subject_width, date_width, length_width) in terminal columns,
    for `width` columns of real terminal after questionary's own row
    gutter (_ROW_GUTTER).

    date_width and length_width are sized from the rows actually being
    shown - the same technique contents.py's own number column uses,
    sized from the actual highest starting-cell number rather than a
    hardcoded guess - so a stray "length unknown" row (17 characters,
    much wider than "[short]") does not silently clip instead of
    widening the column. subject_width takes whatever is left after
    those two fixed columns and two _GAP separators, floored at
    _MIN_SUBJECT_WIDTH so an extreme combination never crushes the
    subject away to nothing.
    """
    effective = max(width, _MIN_TERMINAL_WIDTH) - _ROW_GUTTER
    date_width = max((_display_width(row.when) for row in rows), default=5)
    length_width = max((_display_width(f"[{row.length}]") for row in rows), default=7)
    subject_width = effective - date_width - length_width - 2 * len(_GAP)
    return max(subject_width, _MIN_SUBJECT_WIDTH), date_width, length_width


def _row_text(
    title: str, when: str, length: str, subject_width: int, date_width: int
) -> str:
    """One row's line: subject, date, and length bucket in aligned
    columns - see picker-layout.md point 3. `when` and the bracketed
    `length` are ASCII, so plain str.rjust/str.ljust already measure
    them correctly; the subject is display-width padded by hand because
    it alone may carry a double-width emoji (see _display_width) - and
    sanitized first (see _sanitize_subject) so a stray control character
    in the raw title can never break the terminal's own rendering."""
    subject = _truncate_to_width(_sanitize_subject(title), subject_width)
    subject = subject + " " * (subject_width - _display_width(subject))
    return f"{subject}{_GAP}{when.rjust(date_width)}{_GAP}[{length}]"


@dataclass(frozen=True, slots=True)
class PickLine:
    """One rendered line of the picklist: a blank spacer, a group
    heading, or a selectable row - plain data, ready for pickerui.py to
    map straight onto questionary's own Separator/Choice with no
    formatting decisions left for it to make (see this module's own
    docstring on why that boundary matters for coverage).

    `document` is None for "blank" and "heading" (nothing to select) and
    the real candidate for "row".
    """

    kind: Literal["blank", "heading", "row"]
    text: str
    document: Document | None = None


def layout_picklist(picklist: Picklist, width: int) -> tuple[PickLine, ...]:
    """Render `picklist` into terminal lines: a blank line and a ruled,
    uppercase heading before every group (including the first - "A blank
    line before each heading", picker-layout.md point 1), then one
    aligned row per candidate.

    `width` is normally the real terminal's column count
    (shutil.get_terminal_size().columns, read by pickerui.py so this
    stays a pure function any test can drive with any width - see
    pickerui.py's own module docstring on why the library boundary is
    drawn there). Column widths are computed once across the *whole*
    picklist, not per group, so the date/length columns line up all the
    way down a scrollable list of many publications, not just within one
    group.
    """
    all_rows = [row for group in picklist.groups for row in group.rows]
    subject_width, date_width, _length_width = _column_widths(all_rows, width)
    heading_width = max(width, _MIN_TERMINAL_WIDTH) - _HEADING_GUTTER

    lines: list[PickLine] = []
    for group in picklist.groups:
        lines.append(PickLine(kind="blank", text=" "))
        lines.append(
            PickLine(
                kind="heading",
                text=_heading_rule(group.publication, group.source, heading_width),
            )
        )
        for row in group.rows:
            lines.append(
                PickLine(
                    kind="row",
                    text=_row_text(
                        row.document.title,
                        row.when,
                        row.length,
                        subject_width,
                        date_width,
                    ),
                    document=row.document,
                )
            )
    return tuple(lines)
