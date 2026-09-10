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

import re
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
# The gap before the packet cell number, which is deliberately much wider
# than SEGMENT_GAP_PT. The counter ("10/13") used to sit centred in the
# cell, which wasted ~70pt of dead space to its right while the subject on
# the left was being cut mid-word. Moving it right recovers that space,
# but it then sits beside another number ("361 · 9 Sep 2026"), and two
# numbers a normal word-space apart read as one field. Four times the
# ordinary gap keeps them legibly separate.
COUNTER_GAP_PT = 24.0
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
    name rather than repeating it. Substring containment alone misses a
    duplicate where the author is the publication's name with something
    inserted in the middle rather than appended at an end - e.g. "Ruth
    Ben-Ghiat from Lucid" byline "Ruth Ben-Ghiat and Joyce Vance from
    Lucid" (a co-author spliced into the middle) - so _shares_most_words
    also catches substantial word-for-word overlap.
    """
    if not author:
        return publication
    pub = publication.casefold()
    who = author.casefold()
    if pub in who or who in pub or _shares_most_words(pub, who):
        return publication
    return f"{publication} · {author}"


# A subject can carry an embedded CRLF from an unfolded header
# continuation - the picker already defends against the same dirty data
# (see picker._sanitize_subject). Here it would not corrupt the line the
# way it does in a terminal, but it would still measure and draw as
# whitespace inside the footer, so it is collapsed to single spaces.
_WHITESPACE_RUN = re.compile(r"\s+")


# A leading "<name><separator>" on a subject line. Bounded at 40
# characters so this only ever considers a real prefix, never half a
# sentence that happens to contain a dash.
_SUBJECT_PREFIX = re.compile(r"^(.{2,40}?)\s*[:\u2013\u2014|-]\s+")


def _strip_repeated_publication(subject: str, publication: str) -> str:
    """Drop a subject's leading repeat of its own publication name.

    Several newsletters prefix every subject with their own name - "The
    Morning: The word is bond", "DealBook: Is $100 oil coming?", "Data
    Elixir - Issue 572". In the footer that name is already the first
    thing on the line, so the repeat spends a third of a very small
    budget saying it twice; a packet carrying seven issues of The Morning
    pays it seven times. Measured across the 268 archived newsletters, 14
    subjects do this.

    Only an actual repeat is removed: the prefix and the publication must
    contain one another, so "Axios AM: ..." under the byline "Mike Allen"
    keeps its prefix, which is genuinely the only thing naming that
    newsletter. Never returns empty - a subject that is *only* its own
    publication name is left alone rather than reduced to nothing.
    """
    match = _SUBJECT_PREFIX.match(subject)
    if match is None:
        return subject
    prefix = match.group(1).casefold().strip()
    name = publication.casefold().strip()
    if not prefix or not (prefix in name or name in prefix):
        return subject
    return subject[match.end() :].strip() or subject


def footer_left(publication: str, author: str | None, title: str) -> str:
    """The footer's left segment: who wrote it, then what this one is.

    The byline alone answers "which newsletter" but not "which issue" -
    on a packet of several issues from the same publication (The Morning
    runs seven in a week) every footer read identically. The subject is
    appended after the byline so the byline, the part that identifies the
    publication, is what survives when _draw_footer truncates the segment
    to fit; the subject is what gets cut, which is the right way round.

    Separated with the same "·" byline itself uses: it is Latin-1, so the
    base-14 "helv" font really has it. An em dash measures the same width
    as a middle dot in that font, which is how you can tell it is being
    silently substituted - the same trap ELLIPSIS documents.
    """
    line = byline(publication, author)
    subject = _strip_repeated_publication(
        _WHITESPACE_RUN.sub(" ", title).strip(), publication
    )
    return f"{line} · {subject}" if subject else line


def _shares_most_words(a: str, b: str) -> bool:
    """True when the shorter of two (already casefolded) names has most
    of its words - strictly more than half - also present in the longer
    one, order and position ignored.

    Deliberately conservative: a bare majority is required, not "any
    shared word", so two names that merely share one common word out of
    two (e.g. two different people who both go by "Jane") stay distinct.
    Word overlap is order-independent because the extra content in the
    longer name (an inserted co-author, a trailing affiliation) need not
    land at either end - a purely prefix/suffix check would miss exactly
    the "inserted in the middle" case this exists to catch.
    """
    words_a = set(re.findall(r"[\w'-]+", a))
    words_b = set(re.findall(r"[\w'-]+", b))
    if not words_a or not words_b:
        return False
    shorter, longer = (
        (words_a, words_b)
        if len(words_a) <= len(words_b)
        else (
            words_b,
            words_a,
        )
    )
    return len(shorter & longer) / len(shorter) > 0.5


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
    counter: str,
    right: str,
) -> None:
    """Draw the three footer segments.

    `left` is the byline and subject, `counter` the page's position
    within its own newsletter, `right` the packet cell number and date.
    Only `left` is truncated: it is the one segment whose length is not
    known in advance, and the two on the right are the ones a reader
    navigates by.
    """
    scale = POINTS_PER_INCH / MM_PER_INCH
    cell_width_pt, cell_height_pt = paper.cell.as_points()
    margin_pt = layout.margin_mm * scale
    # Halfway down the bottom margin band: comfortably clear of both the
    # page box's content area above it and the sheet edge below it.
    baseline_y = cell_height_pt - margin_pt / 2

    right_width = pymupdf.get_text_length(right, fontname=FONT, fontsize=FONT_SIZE_PT)
    counter_width = pymupdf.get_text_length(
        counter, fontname=FONT, fontsize=FONT_SIZE_PT
    )
    right_x = cell_width_pt - margin_pt - right_width
    # Right-aligned against the date rather than centred in the cell, so
    # every column the counter is not using goes to the subject. Clamped
    # at the left margin so a pathologically narrow cell degrades to
    # overlapping text rather than negative coordinates.
    counter_x = max(margin_pt, right_x - COUNTER_GAP_PT - counter_width)
    left_max_width = max(0.0, counter_x - SEGMENT_GAP_PT - margin_pt)
    left_text = _truncate(left, left_max_width)

    for text, x in ((left_text, margin_pt), (counter, counter_x), (right, right_x)):
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
        left = footer_left(
            item.document.publication,
            item.document.author,
            item.document.title,
        )
        with pymupdf.open(item.pdf) as document:
            total = document.page_count
            for page_index in range(total):
                counter = f"{page_index + 1}/{total}"
                right = f"{offset + page_index + 1} · {date_text}"
                _draw_footer(document[page_index], paper, layout, left, counter, right)
            output = out_dir / f"{index:03d}-stamped.pdf"
            document.save(output)
        stamped.append(output)
        offset += total
    return stamped
