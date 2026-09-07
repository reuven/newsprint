"""Decide what to do about a document's final cell.

The judgment is about what the text *is*, never how much of it there is, and
the error is deliberately asymmetric: keeping a junk cell wastes a quarter of
one side of one sheet, while dropping a content cell destroys something the
reader wanted. So the test is "is there any real content here?".
"""

from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .boilerplate import content_ratio
from .config import LayoutConfig
from .geometry import Paper
from .models import Verdict
from .pdfutil import page_count, page_text, text_extent_mm

FILLER_RATIO = 0.15
WIDOW_FILL = 0.20
COMPRESSIONS = (0.99, 0.98)


def classify(
    pdf: Path,
    paper: Paper,
    layout: LayoutConfig,
    *,
    filler_ratio: float = FILLER_RATIO,
    widow_fill: float = WIDOW_FILL,
) -> Verdict:
    last = page_count(pdf) - 1
    text = page_text(pdf, last)
    if not text.strip():
        return Verdict.FILLER
    if content_ratio(text) < filler_ratio:
        return Verdict.FILLER

    usable_mm = paper.cell.height_mm - 2 * layout.margin_mm
    used_mm = max(0.0, text_extent_mm(pdf, last, layout.margin_mm) - layout.margin_mm)
    if used_mm / usable_mm < widow_fill:
        return Verdict.WIDOW
    return Verdict.FULL


def _without_last_page(pdf: Path) -> Path:
    reader = PdfReader(str(pdf))
    writer = PdfWriter()
    for page in reader.pages[:-1]:
        writer.add_page(page)
    output = pdf.with_name(f"{pdf.stem}-trimmed.pdf")
    with output.open("wb") as handle:
        writer.write(handle)
    return output


def fit(
    pdf: Path,
    paper: Paper,
    layout: LayoutConfig,
    rerender: Callable[[float], Path],
) -> tuple[Path, Verdict]:
    """Return the PDF to use, and what was actually done to get it."""
    original_pages = page_count(pdf)
    if original_pages <= 1:
        # Never leave a document with no pages at all.
        return pdf, Verdict.FULL

    verdict = classify(pdf, paper, layout)
    if verdict is Verdict.FILLER:
        return _without_last_page(pdf), Verdict.FILLER

    if verdict is Verdict.WIDOW:
        for compression in COMPRESSIONS:
            candidate = rerender(compression)
            if page_count(candidate) < original_pages:
                return candidate, Verdict.WIDOW

    return pdf, Verdict.FULL
