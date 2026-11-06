"""Known-answer tests for the printing display.

The worked example throughout is the 2026-10-30 run: 420 cells on 105
sheet sides, printed duplex. 105 is odd, so the printer ran 53 sheets -
106 impressions, the last one the blank back of sheet 53 - and reported
106 against a document of 105. The print was perfect; the report was not.
"""

import pytest

from newsprint.progress import (
    format_elapsed,
    printed_line,
    printing_line,
    queued_line,
    queueing_line,
    remaining,
    sheets_for,
    stall_lines,
)

DUPLEX = "two-sided-long-edge"
SIMPLEX = "one-sided"


# --- duplex arithmetic ---------------------------------------------------


@pytest.mark.parametrize(
    ("sides", "sheets"),
    [
        (105, 53),  # the 2026-10-30 run: an odd side out is still a sheet
        (106, 53),
        (104, 52),
        (1, 1),
        (0, 0),
    ],
)
def test_duplex_sheets(sides: int, sheets: int) -> None:
    assert sheets_for(sides, DUPLEX) == sheets


def test_simplex_is_one_sheet_per_side() -> None:
    assert sheets_for(105, SIMPLEX) == 105


# --- elapsed -------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0s"),
        (45, "45s"),
        (59.9, "59s"),  # truncated, never rounded up into the next unit
        (60, "1m00s"),
        (432, "7m12s"),  # the screenshot
        (862, "14m22s"),  # what the whole run took
        (3600, "1h00m"),
        (3661, "1h01m"),
    ],
)
def test_format_elapsed(seconds: float, text: str) -> None:
    assert format_elapsed(seconds) == text


# --- estimate ------------------------------------------------------------


def test_remaining_extrapolates_from_the_rate_so_far() -> None:
    """94 sides in 432s is 0.218 sides/s, so the last 11 need about 50s."""
    assert remaining(94, 105, 432) == "~1m left"


def test_remaining_rounds_up_to_a_whole_minute() -> None:
    """Never promise "0m left" to someone watching a printer."""
    assert remaining(104, 105, 400) == "~1m left"


def test_remaining_on_a_long_job() -> None:
    assert remaining(20, 105, 180) == "~13m left"


@pytest.mark.parametrize(
    ("done", "total", "seconds"),
    [
        (0, 105, 30),  # nothing printed yet: no rate to extrapolate from
        (105, 105, 400),  # already there
        (106, 105, 400),  # past the end, via the blank back
        (40, 105, 0),  # no time has passed
    ],
)
def test_remaining_declines_to_guess(done: int, total: int, seconds: float) -> None:
    assert remaining(done, total, seconds) is None


# --- the lines themselves ------------------------------------------------


def test_printing_line_midway() -> None:
    """The shape approved for this change, against the screenshot's own
    numbers."""
    assert (
        printing_line(94, 105, DUPLEX, 432)
        == "Printing: sheet 47 of 53  (94 sides)  7m12s, ~1m left"
    )


def test_printing_line_never_exceeds_the_document() -> None:
    """The bug this branch exists for: the printer says 106 of a 105-side
    document, because it ran the blank back of the last sheet. Showing
    "106/105" reads as a fault in a run that went perfectly."""
    assert (
        printing_line(106, 105, DUPLEX, 862)
        == "Printing: sheet 53 of 53  (105 sides)  14m22s"
    )


def test_printing_line_drops_the_estimate_when_it_cannot_guess() -> None:
    assert printing_line(0, 105, DUPLEX, 4) == "Printing: sheet 0 of 53  (0 sides)  4s"


def test_printing_line_says_one_side_not_one_sides() -> None:
    """A one-side packet is rare and the grammar still has to hold."""
    assert printing_line(1, 1, DUPLEX, 3) == "Printing: sheet 1 of 1  (1 side)  3s"


def test_printing_line_simplex() -> None:
    assert (
        printing_line(40, 105, SIMPLEX, 200)
        == "Printing: sheet 40 of 105  (40 sides)  3m20s, ~5m left"
    )


def test_queueing_line_counts_what_cups_has_sent() -> None:
    assert queueing_line(94, 105) == "Queueing: 94 of 105 sides..."


def test_queueing_line_is_also_clamped() -> None:
    assert queueing_line(106, 105) == "Queueing: 105 of 105 sides..."


def test_queueing_and_queued_lines_agree_with_a_count_of_one() -> None:
    assert queueing_line(1, 1) == "Queueing: 1 of 1 side..."
    assert queued_line(1) == "Queued: 1 side sent to the printer."


def test_queued_line_is_past_tense_and_final() -> None:
    """It replaces the Queueing line, so it has to read as done - and has
    to say "to the printer", because the print queue is empty from here on
    while the printer still has minutes of paper to go."""
    assert queued_line(105) == "Queued: 105 sides sent to the printer."


def test_printed_line_speaks_in_the_documents_terms() -> None:
    """Not "Printed 106 sides.", which is the printer counting a blank
    back nobody asked for."""
    assert printed_line(105, DUPLEX) == "Printed 105 sides on 53 sheets."


def test_printed_line_simplex() -> None:
    assert printed_line(12, SIMPLEX) == "Printed 12 sides on 12 sheets."


def test_printed_line_singular_sheet() -> None:
    assert printed_line(1, DUPLEX) == "Printed 1 side on 1 sheet."


# --- the stall warning --------------------------------------------------


def test_stall_lines_lead_with_the_frozen_count() -> None:
    """The 2026-11-05 run: stuck at 37 of 72 after the tray ran dry. The
    first line has to carry both numbers and the duration, because it is
    the line that answers "is it printing or not?"."""
    assert stall_lines(37, 72, 600)[0] == (
        "No change for 10m00s - stuck at 37 of 72 sides."
    )


def test_stall_lines_say_one_side_not_one_sides() -> None:
    assert stall_lines(1, 1, 600)[0] == "No change for 10m00s - stuck at 1 of 1 side."


def test_stall_lines_name_what_to_check() -> None:
    """The warning earns its place only if it shortens the diagnosis. On
    2026-11-05 the answer took a socket dump; these are the four things
    that would have gotten there in a minute."""
    body = " ".join(stall_lines(37, 72, 600)[1:]).lower()
    assert "paper" in body
    assert "lpstat" in body
    assert "cancel" in body
    assert "ctrl-c" in body


def test_stall_lines_promise_the_mail_is_safe() -> None:
    """Last week an interrupted run left mail to clean up by hand, so the
    person deciding whether to hit Ctrl-C needs this said out loud."""
    assert any("no mail" in line.lower() for line in stall_lines(37, 72, 600))


def test_stall_lines_are_separate_lines_not_one_blob() -> None:
    """The caller prefixes each line, so none of them may carry their own
    newlines or indentation."""
    lines = stall_lines(37, 72, 600)
    assert len(lines) > 1
    assert all("\n" not in line for line in lines)
    assert all(line == line.strip() for line in lines)


def test_stall_lines_clamp_like_every_other_line_here() -> None:
    """The module's whole premise: never show a fraction over 100%. A
    stall at the blank back of the last sheet is far-fetched, but the
    invariant should not have an exception nobody remembers."""
    assert stall_lines(106, 105, 600)[0] == (
        "No change for 10m00s - stuck at 105 of 105 sides."
    )


def test_remaining_estimates_from_a_single_side() -> None:
    """One side is a rate, thin but real: 1 side in 10s leaves 104 to go
    at 0.1/s, about 17 minutes. The guard is `done <= 0`, not `done <= 1`,
    and only a test at the boundary says which."""
    assert remaining(1, 105, 10) == "~17m left"


def test_remaining_estimates_inside_the_first_second() -> None:
    """Likewise `seconds <= 0`: a fraction of a second is still elapsed
    time, and 40 sides in one second leaves well under a minute."""
    assert remaining(40, 105, 1) == "~1m left"
