"""What to print again, when a job stopped before the end of the packet.

A reprint is not an ordinary print of fewer pages, and the difference is
the whole reason this module exists. CUPS lays out whatever page range it
is given from scratch: page 1 of the extracted range becomes the front of
a sheet, whatever it was in the original document. Hand it an even side -
a back - and every back prints as a front, so the remainder of the packet
comes out inside-out from there to the end. Nothing in the output looks
wrong until the paper is in hand.

So the arithmetic here is in sheets, never in sides. The resume point is
the first side of the first sheet that is not already whole, `2S + 1` for
S whole sheets, and a sheet caught half-finished simply goes through
again. That costs one sheet of paper. Getting it wrong cost 36.

The numbers are also read off paper rather than reported by software,
because when a job dies this way the software has nothing left to say -
the printer threw the job away and CUPS kept calling it healthy. What a
person has is a stack of paper with cell numbers stamped on it, so
`resume_after_cell` takes the last cell that came out whole and does the
rest. On 2026-11-05 that was cell 144 of 287; everything else followed
from it.
"""

import re
from dataclasses import dataclass

from .progress import is_duplex, sheets_for

# One range, not lp's "1,3-5": a scattered list cannot be checked against
# the sheet rule at all, since every piece of it would need its own.
_RANGE = re.compile(r"^(\d+)(-)?(\d+)?$")


class ReprintError(Exception):
    """The range cannot be printed, with the reason stated for a reader.

    Raised rather than corrected. Every one of these means the reprint
    would have wasted paper in a way that is not visible until the stack
    is in hand, and the person holding it is better placed than this code
    to say which pages they actually have.
    """


@dataclass(frozen=True, slots=True)
class Pages:
    """An inclusive range of sheet sides, one-based, as CUPS counts them."""

    first: int
    last: int

    @property
    def count(self) -> int:
        return self.last - self.first + 1

    def __str__(self) -> str:
        """The `page-ranges` spelling, which is also how a person says it."""
        return f"{self.first}-{self.last}"


def resume_at(sides_printed: int, duplex: str) -> int:
    """The side a reprint must start on, after `sides_printed` came out.

    Rounded down to a whole sheet on a duplex job, because a duplex job
    cannot start on a back: a sheet interrupted between its front and its
    back is printed again in full.
    """
    if not is_duplex(duplex):
        return sides_printed + 1
    return 2 * (sides_printed // 2) + 1


def resume_after_cell(cell: int, cells_per_side: int, duplex: str) -> int:
    """The side to resume at, given the last cell number that came out.

    The cell numbers are what the reader can actually see - they are
    stamped on the paper - and imposition is strictly sequential, so cell
    N is on side `ceil(N / cells_per_side)`. A side only counts as
    printed if all of its cells are, hence floor division: a side caught
    part way through is reprinted, and with it the rest of its sheet.

    `cells_per_side` is the layout's own 2 or 4; nothing else is a layout
    this project produces.
    """
    return resume_at(cell // cells_per_side, duplex)


def parse_pages(text: str, total: int, duplex: str) -> Pages:
    """Read "37-72", "37-" or "37" against a document of `total` sides.

    Validated here rather than at the printer, because every mistake this
    catches is one whose cost is a stack of wasted paper that looks right
    until it is read.
    """
    written = "".join(text.split())
    if "," in written:
        raise ReprintError(
            "newsprint reprints one range of sides, not a list of them: a "
            "scattered list cannot be checked against the sheet rule, since "
            "every piece of it would start a fresh sheet of its own."
        )
    match = _RANGE.match(written)
    if match is None:
        raise ReprintError(
            f"{text!r} is not a page range. Write it as 37-72, as 37- for "
            "everything from side 37 on, or as 37 for a single side."
        )
    first = int(match.group(1))
    if match.group(2) is None:
        last = first
    elif match.group(3) is None:
        last = total
    else:
        last = int(match.group(3))

    if first < 1:
        raise ReprintError("A page range starts at page 1, not 0.")
    if first > total or last > total:
        raise ReprintError(
            f"The packet has only {total} sides, so {first}-{last} runs off "
            "the end of it."
        )
    if last < first:
        raise ReprintError(f"{first}-{last} ends before it starts.")
    if is_duplex(duplex) and first % 2 == 0:
        raise ReprintError(
            f"Page {first} is the back of a sheet, and a duplex reprint "
            "cannot start on a back: CUPS lays out whatever range it is "
            "given from scratch, so every back would print as a front and "
            "the rest of the packet would come out inside-out.\n"
            f"Start on an odd side instead - --pages {first - 1}-{last} "
            "reprints one sheet you already have and gets the rest right."
        )
    return Pages(first=first, last=last)


def pages_after_cell(cell: int, cells_per_side: int, total: int, duplex: str) -> Pages:
    """The range that finishes a `total`-side packet, given the last cell.

    Checked against the packet because a mistyped cell number is easy and
    the consequence is not: a range starting past the end is one CUPS is
    entitled to read as the whole document.
    """
    first = resume_after_cell(cell, cells_per_side, duplex)
    if first > total:
        raise ReprintError(
            f"Cell {cell} is past the end of this packet, which has only "
            f"{total} sides - {total * cells_per_side} cells at "
            f"{cells_per_side} to a side."
        )
    return Pages(first=first, last=total)


def describe(pages: Pages, total: int, duplex: str) -> str:
    """One line for what a reprint is about to put on paper.

    In sides and sheets both, because the range is in sides and the tray
    is in sheets, and the reader is about to count the tray.
    """
    sheets = sheets_for(pages.count, duplex)
    return (
        f"Sides {pages} of {total} - "
        f"{pages.count} {'side' if pages.count == 1 else 'sides'} on "
        f"{sheets} {'sheet' if sheets == 1 else 'sheets'}."
    )
