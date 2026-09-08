"""Stamp a running per-cell footer onto every page of the finished packet.

The footer carries the newsletter's name and author, its position within
its own document, its position within the whole packet, and the packet
date. It cannot be drawn by CSS in render.py: WeasyPrint's @bottom-center
knows a document's own page count but not how many cells preceded it in
the packet, and trim.py can still drop a filler cell or squeeze a widow
away *after* render() has already committed to a page count. So this
stage runs once every document's final cell count is known - after trim,
before impose - walking the built documents in packet order and stamping
each page with the running totals as it goes.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pymupdf

from .config import LayoutConfig
from .geometry import MM_PER_INCH, POINTS_PER_INCH, Paper
from .mail import _IMAP_MONTHS as _MONTHS
from .pipeline import Built

FONT = "helv"
FONT_SIZE_PT = 6.0
# #555555, the same grey render.py's old @bottom-center counter used.
COLOR = (0x55 / 0xFF, 0x55 / 0xFF, 0x55 / 0xFF)
SEGMENT_GAP_PT = 6.0
# Not "…": PyMuPDF's base-14 "helv" font silently substitutes a single
# middle dot for U+2026, which reads as a typo rather than a truncation.
ELLIPSIS = "..."


def format_packet_date(day: date) -> str:
    """`%-d %b %Y`, without strftime's locale-dependent `%b`.

    A Hebrew locale already turned `%b` into "ספט׳" once, where IMAP (and
    now this footer) needs "Sep" regardless of the host's locale - see
    mail._IMAP_MONTHS. Mirrors that same fix here instead of reintroducing
    the bug in a second place.
    """
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def byline(publication: str, author: str | None) -> str:
    """`Publication · Author`, or bare `Publication` when the author
    duplicates it.

    Compared case-insensitively, and either name containing the other
    counts as a duplicate - "Axios Macro" byline "Axios Macro" and "The
    Bulwark" byline "The Bulwark Podcast" must both collapse to a single
    name rather than repeating it.
    """
    if not author:
        return publication
    pub = publication.casefold()
    who = author.casefold()
    if pub in who or who in pub:
        return publication
    return f"{publication} · {author}"


def _truncate(text: str, max_width_pt: float) -> str:
    """Shorten `text` with a trailing ellipsis until it fits max_width_pt.

    The numbers around it are never truncated - only this, the left
    segment, yields space when the cell is too narrow.
    """
    if not text:
        return text
    if (
        pymupdf.get_text_length(text, fontname=FONT, fontsize=FONT_SIZE_PT)
        <= max_width_pt
    ):
        return text
    ellipsis_width = pymupdf.get_text_length(
        ELLIPSIS, fontname=FONT, fontsize=FONT_SIZE_PT
    )
    if max_width_pt <= ellipsis_width:
        return ""
    truncated = text
    while truncated and (
        pymupdf.get_text_length(truncated, fontname=FONT, fontsize=FONT_SIZE_PT)
        + ellipsis_width
        > max_width_pt
    ):
        truncated = truncated[:-1]
    return truncated.rstrip() + ELLIPSIS


def _draw_footer(
    page: pymupdf.Page,
    paper: Paper,
    layout: LayoutConfig,
    left: str,
    centre: str,
    right: str,
) -> None:
    scale = POINTS_PER_INCH / MM_PER_INCH
    cell_width_pt, cell_height_pt = paper.cell.as_points()
    margin_pt = layout.margin_mm * scale
    # Halfway down the bottom margin band: comfortably clear of both the
    # page box's content area above it and the sheet edge below it.
    baseline_y = cell_height_pt - margin_pt / 2

    right_width = pymupdf.get_text_length(right, fontname=FONT, fontsize=FONT_SIZE_PT)
    centre_width = pymupdf.get_text_length(centre, fontname=FONT, fontsize=FONT_SIZE_PT)
    right_x = cell_width_pt - margin_pt - right_width
    centre_x = (cell_width_pt - centre_width) / 2
    left_max_width = max(0.0, centre_x - SEGMENT_GAP_PT - margin_pt)
    left_text = _truncate(left, left_max_width)

    for text, x in ((left_text, margin_pt), (centre, centre_x), (right, right_x)):
        if text:
            page.insert_text(
                (x, baseline_y),
                text,
                fontsize=FONT_SIZE_PT,
                fontname=FONT,
                color=COLOR,
            )


def stamp_packet(
    built: Sequence[Built],
    packet_date: date,
    paper: Paper,
    layout: LayoutConfig,
    out_dir: Path,
) -> list[Path]:
    """Stamp every page of every document, in packet order.

    Returns the stamped PDF paths in the same order, one per input
    document, for impose() to tile - it already flattens each document's
    own pages when imposing, so this stage does not need to.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    date_text = format_packet_date(packet_date)
    stamped: list[Path] = []
    offset = 0
    for index, item in enumerate(built):
        left = byline(item.document.publication, item.document.author)
        with pymupdf.open(item.pdf) as document:
            total = document.page_count
            for page_index in range(total):
                centre = f"{page_index + 1}/{total}"
                right = f"{offset + page_index + 1} · {date_text}"
                _draw_footer(document[page_index], paper, layout, left, centre, right)
            output = out_dir / f"{index:03d}-stamped.pdf"
            document.save(output)
        stamped.append(output)
        offset += total
    return stamped
