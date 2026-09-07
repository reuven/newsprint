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


def text_extent_mm(path: Path, index: int, margin_mm: float) -> float:
    """Millimetres from the top of the page to the bottom of its lowest text.

    PyMuPDF reports block coordinates with the origin at the top left, so the
    largest y1 is the bottom of the text. The page-number counter that
    render.py places in the bottom margin is excluded by position, not
    content: any block whose top falls at or below the margin boundary is
    footer territory, since WeasyPrint's page box confines real content
    above it. Excluding by content (e.g. "looks numeric") would also discard
    genuine prose that happens to be a year, a statistic, or a footnote
    marker.
    """
    scale = POINTS_PER_INCH / MM_PER_INCH
    margin_pt = margin_mm * scale
    with pymupdf.open(path) as document:
        page = document[index]
        footer_boundary_pt = page.rect.height - margin_pt
        blocks = [
            block
            for block in page.get_text("blocks")
            if block[4].strip() and block[1] < footer_boundary_pt
        ]
    if not blocks:
        return 0.0
    bottom_pt = max(block[3] for block in blocks)
    return bottom_pt / scale
