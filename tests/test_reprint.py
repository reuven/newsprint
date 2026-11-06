"""The arithmetic of finishing a job that stopped partway.

Every expected value here is computed by hand from the incident it names,
not from the implementation. The sheet rule is the one piece of this
project that cannot be checked by looking at the output: a reprint that
starts on the wrong side looks plausible coming out of the printer and is
only discovered once the whole remainder is inside-out.
"""

import pytest

from newsprint.reprint import (
    Pages,
    ReprintError,
    describe,
    pages_after_cell,
    parse_pages,
    resume_after_cell,
    resume_at,
)

DUPLEX = "two-sided-long-edge"
SHORT_EDGE = "two-sided-short-edge"
SIMPLEX = "one-sided"


def test_pages_counts_its_own_sides() -> None:
    assert Pages(37, 72).count == 36


def test_a_single_page_is_one_side() -> None:
    assert Pages(37, 37).count == 1


def test_pages_reads_as_a_cups_range() -> None:
    assert str(Pages(37, 72)) == "37-72"


# resume_at: S complete sides printed, where does the reprint start?


def test_a_whole_sheet_resumes_on_the_next_front() -> None:
    # 36 sides printed is 18 whole sheets, so the next side is a front.
    assert resume_at(36, DUPLEX) == 37


def test_a_half_finished_sheet_is_printed_again() -> None:
    # 35 sides is 17 whole sheets and one front. Side 36 is that sheet's
    # back, and no duplex job can start on a back - so sheet 18 goes
    # through again, costing one sheet rather than the whole remainder.
    assert resume_at(35, DUPLEX) == 35


def test_nothing_printed_resumes_at_the_beginning() -> None:
    assert resume_at(0, DUPLEX) == 1


def test_one_side_printed_still_resumes_at_the_beginning() -> None:
    assert resume_at(1, DUPLEX) == 1


def test_two_sides_printed_resumes_at_the_third() -> None:
    assert resume_at(2, DUPLEX) == 3


def test_short_edge_duplex_follows_the_same_sheet_rule() -> None:
    assert resume_at(35, SHORT_EDGE) == 35


def test_one_sided_resumes_at_the_very_next_side() -> None:
    # No sheet to be half-finished: every side is its own sheet.
    assert resume_at(35, SIMPLEX) == 36


def test_one_sided_nothing_printed_resumes_at_the_beginning() -> None:
    assert resume_at(0, SIMPLEX) == 1


# resume_after_cell: the number read off the last page in the tray.


def test_the_cell_144_incident() -> None:
    # 2026-11-05: 287 cells over 72 sides, four cells to a side. The last
    # cell to come out whole was 144, which is 36 sides, which is 18
    # sheets - so the reprint ran 37-72 and the two halves met exactly.
    assert resume_after_cell(144, 4, DUPLEX) == 37


def test_a_cell_part_way_down_a_side_reprints_that_sheet() -> None:
    # 143 cells is 35 whole sides; side 36 was interrupted, so its sheet
    # - sheet 18, sides 35 and 36 - prints again.
    assert resume_after_cell(143, 4, DUPLEX) == 35


def test_no_cells_printed_resumes_at_the_beginning() -> None:
    assert resume_after_cell(0, 4, DUPLEX) == 1


def test_one_whole_sheet_of_cells_resumes_at_the_third_side() -> None:
    # Eight cells is two sides, which is one whole sheet.
    assert resume_after_cell(8, 4, DUPLEX) == 3


def test_a_single_finished_side_still_reprints_its_sheet() -> None:
    # Four cells is one side: the front of sheet 1, whose back never
    # printed. Sheet 1 goes again.
    assert resume_after_cell(4, 4, DUPLEX) == 1


def test_two_cells_to_a_side_counts_its_own_sides() -> None:
    # The landscape layout: 144 cells at two a side is 72 sides, 36
    # sheets, so the reprint starts at 73.
    assert resume_after_cell(144, 2, DUPLEX) == 73


def test_one_sided_resumes_on_the_side_after_the_cell() -> None:
    assert resume_after_cell(144, 4, SIMPLEX) == 37


def test_a_partial_side_on_one_sided_reprints_that_side() -> None:
    assert resume_after_cell(143, 4, SIMPLEX) == 36


# parse_pages: what the user typed, against the document and the duplex.


def test_a_range_is_read_as_written() -> None:
    assert parse_pages("37-72", 72, DUPLEX) == Pages(37, 72)


def test_an_open_range_runs_to_the_end_of_the_document() -> None:
    assert parse_pages("37-", 72, DUPLEX) == Pages(37, 72)


def test_a_bare_number_is_one_page() -> None:
    assert parse_pages("37", 72, DUPLEX) == Pages(37, 37)


def test_surrounding_space_is_ignored() -> None:
    assert parse_pages("  37 - 72  ", 72, DUPLEX) == Pages(37, 72)


def test_the_whole_document_is_a_valid_range() -> None:
    assert parse_pages("1-72", 72, DUPLEX) == Pages(1, 72)


def test_nothing_is_not_a_range() -> None:
    with pytest.raises(ReprintError, match="not a page range"):
        parse_pages("   ", 72, DUPLEX)


def test_words_are_not_a_range() -> None:
    with pytest.raises(ReprintError, match="not a page range"):
        parse_pages("thirty-seven", 72, DUPLEX)


def test_a_comma_list_says_so_rather_than_guessing() -> None:
    # lp itself takes "1,3-5". newsprint does not, because a reprint of
    # scattered pages cannot be checked against the sheet rule at all.
    with pytest.raises(ReprintError, match="one range"):
        parse_pages("37-40,45-50", 72, DUPLEX)


def test_page_zero_is_refused() -> None:
    with pytest.raises(ReprintError, match="starts at page 1"):
        parse_pages("0-72", 72, DUPLEX)


def test_a_backwards_range_is_refused() -> None:
    with pytest.raises(ReprintError, match="ends before it starts"):
        parse_pages("72-37", 72, DUPLEX)


def test_a_range_past_the_end_of_the_document_is_refused() -> None:
    with pytest.raises(ReprintError, match="only 72 sides"):
        parse_pages("37-99", 72, DUPLEX)


def test_a_first_page_past_the_end_of_the_document_is_refused() -> None:
    with pytest.raises(ReprintError, match="only 72 sides"):
        parse_pages("99-", 72, DUPLEX)


def test_an_even_first_page_is_refused_on_a_duplex_job() -> None:
    with pytest.raises(ReprintError, match="back of a sheet"):
        parse_pages("38-72", 72, DUPLEX)


def test_the_refusal_names_the_odd_page_to_use_instead() -> None:
    with pytest.raises(ReprintError, match=r"--pages 37-72"):
        parse_pages("38-72", 72, DUPLEX)


def test_the_refusal_explains_what_would_have_happened() -> None:
    with pytest.raises(ReprintError, match="inside-out"):
        parse_pages("38-72", 72, DUPLEX)


def test_short_edge_duplex_refuses_an_even_first_page_too() -> None:
    with pytest.raises(ReprintError, match="back of a sheet"):
        parse_pages("38-72", 72, SHORT_EDGE)


def test_an_even_first_page_is_fine_on_a_one_sided_job() -> None:
    assert parse_pages("38-72", 72, SIMPLEX) == Pages(38, 72)


# pages_after_cell: the range, checked against the packet it came from.


def test_the_cell_144_incident_as_a_range() -> None:
    assert pages_after_cell(144, 4, 72, DUPLEX) == Pages(37, 72)


def test_a_cell_in_the_last_side_still_reprints_its_sheet() -> None:
    # 287 cells is the whole packet; the last side was interrupted, so
    # sheet 36 - sides 71 and 72 - goes again.
    assert pages_after_cell(286, 4, 72, DUPLEX) == Pages(71, 72)


def test_a_cell_number_past_the_end_of_the_packet_is_refused() -> None:
    # A mistyped cell number must not become a range CUPS is entitled to
    # read as the whole document.
    with pytest.raises(ReprintError, match="past the end"):
        pages_after_cell(2870, 4, 72, DUPLEX)


def test_the_refusal_says_how_many_cells_the_packet_holds() -> None:
    with pytest.raises(ReprintError, match="288 cells"):
        pages_after_cell(2870, 4, 72, DUPLEX)


def test_the_last_cell_of_the_packet_leaves_nothing_to_reprint() -> None:
    # 288 cells at four a side is exactly 72 sides, so there is no side
    # left - the range would start at 73.
    with pytest.raises(ReprintError, match="past the end"):
        pages_after_cell(288, 4, 72, DUPLEX)


def test_a_reprint_is_described_in_sides_and_sheets() -> None:
    assert describe(Pages(37, 72), 72, DUPLEX) == (
        "Sides 37-72 of 72 - 36 sides on 18 sheets."
    )


def test_a_single_side_is_described_in_the_singular() -> None:
    assert describe(Pages(1, 1), 1, DUPLEX) == "Sides 1-1 of 1 - 1 side on 1 sheet."


def test_one_sided_sheets_are_counted_as_sides() -> None:
    assert describe(Pages(37, 72), 72, SIMPLEX) == (
        "Sides 37-72 of 72 - 36 sides on 36 sheets."
    )
