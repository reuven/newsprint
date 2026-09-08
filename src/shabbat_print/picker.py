"""Choose additional newsletters from the week's unstarred mail.

The starred queue is always the default packet; this is the "too" in the
user's original request - "I sometimes want to print some of the week's
non-starred newsletters, too." Nothing here cleans or renders a candidate:
that only happens for whatever actually gets picked, in
pipeline.build_one(), exactly as it does for the starred queue - most of
what is shown here will never be picked, so doing that work up front would
make a large week slow for no reason.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .models import Document
from .runlog import last_successful_run
from .stamp import format_packet_date

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
                    when=format_packet_date(document.date.date()),
                    length=length_label(
                        sizes.get(document.origin.uid)
                        if document.origin.uid is not None
                        else None
                    ),
                )
                for document in grouped[publication]
            ),
        )
        for publication in sorted(grouped, key=str.casefold)
    )
    return Picklist(groups=groups, total=total)
