from pathlib import Path

import pymupdf

from shabbat_print.config import LayoutConfig
from shabbat_print.geometry import A4, MM_PER_INCH, POINTS_PER_INCH, Paper
from shabbat_print.models import Verdict
from shabbat_print.pdfutil import page_count
from shabbat_print.trim import classify, fit

LAYOUT = LayoutConfig(margin_mm=9.0, font_size_pt=9.0, line_height=1.35)

FOOTER = "Unsubscribe\nManage your preferences\n© 2026 Test Weekly\nRead more"
PROSE = (
    "The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes."
)


def build(path: Path, pages: list[str], paper: Paper = A4, fill: bool = False) -> Path:
    """Write a PDF of cell-sized pages, each carrying the given text.

    `fill=True` repeats the text 8 times, which is as much as actually fits
    in a cell at this font size (confirmed empirically: PyMuPDF's
    `insert_textbox` inserts nothing at all, not a truncated fit, when the
    text overflows the box, so a repeat count that overflows produces a
    blank page rather than a full one).
    """
    width, height = paper.cell.as_points()
    margin = LAYOUT.margin_mm / MM_PER_INCH * POINTS_PER_INCH
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page(width=width, height=height)
            body = (text + "\n") * (8 if fill else 1)
            page.insert_textbox(
                pymupdf.Rect(margin, margin, width - margin, height - margin),
                body,
                fontsize=9,
            )
        document.save(path)
    return path


def test_a_pure_footer_page_is_filler(tmp_path: Path) -> None:
    pdf = build(tmp_path / "a.pdf", [PROSE, FOOTER])
    assert classify(pdf, A4, LAYOUT) is Verdict.FILLER


def test_a_long_advertising_page_is_still_filler(tmp_path: Path) -> None:
    """Length must not rescue a page that is entirely chrome."""
    pdf = build(tmp_path / "b.pdf", [PROSE, "Sponsored by Acme\n" * 30])
    assert classify(pdf, A4, LAYOUT) is Verdict.FILLER


def test_a_blank_final_page_is_filler(tmp_path: Path) -> None:
    pdf = build(tmp_path / "c.pdf", [PROSE, "   "])
    assert classify(pdf, A4, LAYOUT) is Verdict.FILLER


def test_a_short_prose_page_is_a_widow(tmp_path: Path) -> None:
    pdf = build(tmp_path / "d.pdf", [PROSE, PROSE])
    assert classify(pdf, A4, LAYOUT) is Verdict.WIDOW


def test_a_full_prose_page_is_full(tmp_path: Path) -> None:
    pdf = build(tmp_path / "e.pdf", [PROSE, PROSE], fill=True)
    assert classify(pdf, A4, LAYOUT) is Verdict.FULL


def test_fit_drops_a_filler_page(tmp_path: Path) -> None:
    pdf = build(tmp_path / "f.pdf", [PROSE, FOOTER])
    result, verdict = fit(pdf, A4, LAYOUT, rerender=lambda _: pdf)
    assert verdict is Verdict.FILLER
    assert page_count(result) == 1


def test_fit_never_drops_the_only_page(tmp_path: Path) -> None:
    """A one-page document that is all chrome must not become zero pages."""
    pdf = build(tmp_path / "g.pdf", [FOOTER])
    result, verdict = fit(pdf, A4, LAYOUT, rerender=lambda _: pdf)
    assert verdict is Verdict.FULL
    assert page_count(result) == 1


def test_fit_squeezes_a_widow_away(tmp_path: Path) -> None:
    wide = build(tmp_path / "h.pdf", [PROSE, PROSE])
    narrow = build(tmp_path / "h-tight.pdf", [PROSE])
    calls: list[float] = []

    def rerender(compression: float) -> Path:
        calls.append(compression)
        return narrow

    result, verdict = fit(wide, A4, LAYOUT, rerender=rerender)
    assert verdict is Verdict.WIDOW
    assert result == narrow
    assert calls == [0.99]


def test_fit_reverts_when_squeezing_does_not_help(tmp_path: Path) -> None:
    wide = build(tmp_path / "i.pdf", [PROSE, PROSE])
    calls: list[float] = []

    def rerender(compression: float) -> Path:
        calls.append(compression)
        return wide

    result, verdict = fit(wide, A4, LAYOUT, rerender=rerender)
    assert verdict is Verdict.FULL
    assert result == wide
    assert calls == [0.99, 0.98]


def test_fit_leaves_a_full_document_alone(tmp_path: Path) -> None:
    pdf = build(tmp_path / "j.pdf", [PROSE, PROSE], fill=True)
    result, verdict = fit(pdf, A4, LAYOUT, rerender=lambda _: pdf)
    assert verdict is Verdict.FULL
    assert result == pdf
