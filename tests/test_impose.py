from pathlib import Path

import pymupdf
import pytest

from newsprint.geometry import A4, LETTER, Paper
from newsprint.impose import impose
from newsprint.pdfutil import page_count


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
    """Which quadrant of the sheet a label's center falls in."""
    rects = sheet.search_for(label)
    assert rects, f"{label} not found on this sheet"
    center = rects[0].tl
    vertical = "top" if center.y < sheet.rect.height / 2 else "bottom"
    horizontal = "left" if center.x < sheet.rect.width / 2 else "right"
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
    # Anchored: pytest's match= is a search, so an unanchored pattern
    # still matches a message that has grown a prefix or a tail.
    with pytest.raises(ValueError, match=r"^nothing to impose$"):
        impose([], A4, tmp_path / "sheets.pdf")


def test_readers_are_closed_after_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """impose() previously held one PdfReader open per document for the
    whole write and never closed any of them explicitly, relying on
    garbage collection to eventually release the file descriptor - cheap
    insurance against the fd limit on a run with many newsletters."""
    from newsprint import impose as impose_module

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


def test_each_cell_lands_at_its_exact_offset(tmp_path: Path) -> None:
    """Which quadrant a cell falls in is a coarse measure - it survives a
    cell being off by a point, or by a millimetre, in any direction. What
    the fold and the guillotine care about is the offset itself, so this
    reads the four back off the sheet exactly.

    Every cell carries its label at the same place within itself, so the
    label's position on the sheet is that place plus the cell's own
    origin, and nothing else.
    """
    cells = numbered_cells(tmp_path / "cells.pdf", 4)
    out = tmp_path / "sheets.pdf"
    impose([cells], A4, out)

    cell_width, cell_height = A4.cell.as_points()
    with pymupdf.open(out) as sheets:
        sheet = sheets[0]
        found = {n: sheet.search_for(f"PAGE {n}")[0] for n in range(1, 5)}

    # Reading order: top-left, top-right, bottom-left, bottom-right.
    within_x, within_y = found[1].x0, found[1].y0
    expected = {
        1: (within_x, within_y),
        2: (within_x + cell_width, within_y),
        3: (within_x, within_y + cell_height),
        4: (within_x + cell_width, within_y + cell_height),
    }
    for number, (x, y) in expected.items():
        assert found[number].x0 == pytest.approx(x, abs=0.01), f"cell {number} across"
        assert found[number].y0 == pytest.approx(y, abs=0.01), f"cell {number} down"
