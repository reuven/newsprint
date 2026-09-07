"""Tests for pdfutil helpers that render.py's tests don't reach.

render() always writes a masthead and title, so a page produced through it
never has zero text blocks. text_extent_mm's empty-page branch is only
reachable from a PDF with no text at all, which we build directly here.
"""

from pathlib import Path

import pymupdf

from shabbat_print.pdfutil import text_extent_mm


def test_text_extent_of_a_blank_page_is_zero(tmp_path: Path) -> None:
    document = pymupdf.open()
    document.new_page(width=297.6, height=421.1)
    path = tmp_path / "blank.pdf"
    document.save(path)
    document.close()

    assert text_extent_mm(path, 0, margin_mm=9.0) == 0.0
