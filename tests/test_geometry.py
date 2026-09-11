"""Geometry tests. The central claim is that a cell is a quarter of a sheet."""

import pytest

from newsprint.geometry import A4, LETTER, Paper, paper_by_name


def test_a4_cell_is_a6() -> None:
    assert A4.cell.width_mm == pytest.approx(105.0)
    assert A4.cell.height_mm == pytest.approx(148.5)


def test_letter_cell_is_quarter_letter() -> None:
    assert LETTER.cell.width_mm == pytest.approx(4.25 * 25.4)
    assert LETTER.cell.height_mm == pytest.approx(5.5 * 25.4)


@pytest.mark.parametrize("paper", [A4, LETTER])
def test_four_cells_cover_the_sheet(paper: Paper) -> None:
    assert paper.cell.width_mm * 2 == pytest.approx(paper.sheet.width_mm)
    assert paper.cell.height_mm * 2 == pytest.approx(paper.sheet.height_mm)


@pytest.mark.parametrize("paper", [A4, LETTER])
def test_cell_aspect_matches_sheet(paper: Paper) -> None:
    """A 2x2 grid halves both dimensions, so the shape is preserved."""
    assert paper.cell.aspect == pytest.approx(paper.sheet.aspect)


def test_a4_cell_in_points() -> None:
    width_pt, height_pt = A4.cell.as_points()
    assert width_pt == pytest.approx(297.638, abs=0.01)
    assert height_pt == pytest.approx(420.945, abs=0.01)


@pytest.mark.parametrize("name", ["a4", "A4", "  A4  "])
def test_paper_by_name_is_forgiving(name: str) -> None:
    assert paper_by_name(name) is A4


def test_paper_by_name_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown paper"):
        paper_by_name("foolscap")


def test_four_cells_a_side_quarters_the_sheet() -> None:
    """The default: two across and two down, so each cell is half the
    sheet each way."""
    assert A4.cell.width_mm == pytest.approx(105.0)
    assert A4.cell.height_mm == pytest.approx(148.5)


def test_two_cells_a_side_halves_it_the_long_way() -> None:
    """Two a side is one across and two down - full width, half height -
    so the fold that separates them is the same horizontal fold, and the
    cell is A5 for an A4 sheet rather than a tall half-column.

    This is what doubles the type: the cell is twice the area, and the
    font size in the config is unchanged.
    """
    from dataclasses import replace

    two_up = replace(A4, cells_per_side=2)
    assert two_up.cell.width_mm == pytest.approx(210.0)
    assert two_up.cell.height_mm == pytest.approx(148.5)
    assert two_up.sheet == A4.sheet, "the paper is the same paper"


def test_a_cell_count_the_imposition_cannot_lay_out_is_refused() -> None:
    """Only 2 and 4 are offered. One is "do not impose at all", and eight
    puts the text below anything a person would read on paper."""
    from dataclasses import replace

    for count in (1, 3, 8):
        with pytest.raises(ValueError, match="cells per side"):
            _ = replace(A4, cells_per_side=count).cell
