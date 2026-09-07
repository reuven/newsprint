from pathlib import Path

import pymupdf
import pytest

from shabbat_print.geometry import A4, LETTER, Paper
from shabbat_print.impose import impose
from shabbat_print.pdfutil import page_count


def numbered_cells(path: Path, count: int, paper: Paper = A4) -> Path:
    """A PDF of cell-sized pages, each stamped with its own number."""
    width, height = paper.cell.as_points()
    with pymupdf.open() as document:
        for number in range(1, count + 1):
            page = document.new_page(width=width, height=height)
            page.insert_text((20, 40), f"PAGE {number}", fontsize=14)
        document.save(path)
    return path


def quadrant_of(sheet: pymupdf.Page, label: str) -> tuple[str, str]:
    """Which quadrant of the sheet a label's centre falls in."""
    rects = sheet.search_for(label)
    assert rects, f"{label} not found on this sheet"
    centre = rects[0].tl
    vertical = "top" if centre.y < sheet.rect.height / 2 else "bottom"
    horizontal = "left" if centre.x < sheet.rect.width / 2 else "right"
    return vertical, horizontal


def test_eight_cells_make_two_sheet_sides(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 8)
    out = tmp_path / "sheets.pdf"
    assert impose([cells], A4, out) == 2
    assert page_count(out) == 2


def test_sheet_is_full_paper_size(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 4)
    out = tmp_path / "sheets.pdf"
    impose([cells], A4, out)
    with pymupdf.open(out) as document:
        rect = document[0].rect
    expected_width, expected_height = A4.sheet.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)
    assert rect.height == pytest.approx(expected_height, abs=1.0)


def test_cells_land_in_reading_order(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 8)
    out = tmp_path / "sheets.pdf"
    impose([cells], A4, out)
    with pymupdf.open(out) as document:
        first, second = document[0], document[1]
        assert quadrant_of(first, "PAGE 1") == ("top", "left")
        assert quadrant_of(first, "PAGE 2") == ("top", "right")
        assert quadrant_of(first, "PAGE 3") == ("bottom", "left")
        assert quadrant_of(first, "PAGE 4") == ("bottom", "right")
        assert quadrant_of(second, "PAGE 5") == ("top", "left")
        assert quadrant_of(second, "PAGE 6") == ("top", "right")


def test_several_documents_are_concatenated(tmp_path: Path) -> None:
    first = numbered_cells(tmp_path / "one.pdf", 2)
    second = numbered_cells(tmp_path / "two.pdf", 2)
    out = tmp_path / "sheets.pdf"
    assert impose([first, second], A4, out) == 1


def test_a_partial_sheet_leaves_blank_cells(tmp_path: Path) -> None:
    """The property the name promises: a 5th cell starts a new sheet with
    only its top-left quadrant filled, not just that there are 2 sheets."""
    cells = numbered_cells(tmp_path / "cells.pdf", 5)
    out = tmp_path / "sheets.pdf"
    assert impose([cells], A4, out) == 2
    with pymupdf.open(out) as document:
        second = document[1]
        assert quadrant_of(second, "PAGE 5") == ("top", "left")
        for label in ("PAGE 6", "PAGE 7", "PAGE 8"):
            assert not second.search_for(label), (
                f"{label} must not appear on a sheet with only one real cell"
            )


def test_letter_paper_gives_letter_sheets(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 4, paper=LETTER)
    out = tmp_path / "sheets.pdf"
    impose([cells], LETTER, out)
    with pymupdf.open(out) as document:
        rect = document[0].rect
    expected_width, expected_height = LETTER.sheet.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)
    assert rect.height == pytest.approx(expected_height, abs=1.0)


def test_imposing_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nothing to impose"):
        impose([], A4, tmp_path / "sheets.pdf")


def test_readers_are_closed_after_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """impose() previously held one PdfReader open per document for the
    whole write and never closed any of them explicitly, relying on
    garbage collection to eventually release the file descriptor - cheap
    insurance against the fd limit on a run with many newsletters."""
    from shabbat_print import impose as impose_module

    cells = numbered_cells(tmp_path / "cells.pdf", 4)
    created: list = []
    original = impose_module.PdfReader

    def tracking(*args, **kwargs):
        reader = original(*args, **kwargs)
        created.append(reader)
        return reader

    monkeypatch.setattr(impose_module, "PdfReader", tracking)

    out = tmp_path / "sheets.pdf"
    impose_module.impose([cells], A4, out)

    assert created
    assert all(reader.stream.closed for reader in created)
