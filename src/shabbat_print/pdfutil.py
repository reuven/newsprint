"""Reading facts back out of a rendered PDF."""

from pathlib import Path

import pymupdf

from .geometry import MM_PER_INCH, POINTS_PER_INCH


def page_count(path: Path) -> int:
    with pymupdf.open(path) as document:
        return document.page_count


def page_text(path: Path, index: int) -> str:
    with pymupdf.open(path) as document:
        return document[index].get_text()


def text_extent_mm(path: Path, index: int) -> float:
    """Millimetres from the top of the page to the bottom of its lowest text.

    PyMuPDF reports block coordinates with the origin at the top left, so the
    largest y1 is the bottom of the text. The page-number counter that
    render.py places in the bottom margin is excluded: it is always a bare
    digit string, and counting it would make every page measure as full
    regardless of how much of the page its content actually fills.
    """
    with pymupdf.open(path) as document:
        blocks = [
            block
            for block in document[index].get_text("blocks")
            if block[4].strip() and not block[4].strip().isdigit()
        ]
    if not blocks:
        return 0.0
    bottom_pt = max(block[3] for block in blocks)
    return bottom_pt / POINTS_PER_INCH * MM_PER_INCH
