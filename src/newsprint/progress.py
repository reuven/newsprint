"""What the terminal says while the printer works.

Printing a packet takes a quarter of an hour, during which the print queue
is empty - CUPS marks the job finished the moment the printer's memory has
the document, and the printer then works alone. So these lines are the
only evidence the run is alive, and on 2026-10-30 they failed at it: a
105-side packet printed perfectly and reported "Printed 106 sides."

That 106 is real. 105 is odd, so printing both sides runs 53 sheets, and
the back of the 53rd is blank; the printer counts it. Every number here is
therefore clamped to the document's own totals, because a reader counting
pages in a tray does not care that the printer ran one blank side, and
"106/105" reads as a fault in a run that went perfectly.

Nothing in here reads a clock. Elapsed seconds arrive as an argument, so
the tests need no clock either, and the caller keeps the one source of
time.
"""

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


def is_duplex(duplex: str) -> bool:
    """Whether a `sides=` value names a two-sided mode.

    One line, in one place, because two things turn on it that must never
    disagree: how many sheets a side count is (here), and which sides a
    reprint may start on (reprint.py). Long-edge and short-edge differ
    only in which way the back is turned, which neither question cares
    about.
    """
    return duplex.startswith("two-sided")


def sheets_for(sides: int, duplex: str) -> int:
    """Sheets of paper `sides` sides take.

    Printing both sides, an odd side out is still a whole sheet that came
    out of the printer - which is why a 105-side packet is 53 sheets and
    the printer reports 106 impressions.
    """
    if not is_duplex(duplex):
        return sides
    return -(-sides // 2)


def format_elapsed(seconds: float) -> str:
    """How long the job has been going, as "45s", "7m12s" or "1h01m".

    Truncates rather than rounds: a clock that reads 1m00s after 59.9
    seconds has told a small lie about something the reader is watching.
    """
    whole = int(seconds)
    if whole < _SECONDS_PER_MINUTE:
        return f"{whole}s"
    if whole < _SECONDS_PER_HOUR:
        return f"{whole // _SECONDS_PER_MINUTE}m{whole % _SECONDS_PER_MINUTE:02d}s"
    hours, rest = divmod(whole, _SECONDS_PER_HOUR)
    return f"{hours}h{rest // _SECONDS_PER_MINUTE:02d}m"


def remaining(done: int, total: int, seconds: float) -> str | None:
    """ "~13m left", extrapolated from the rate so far, or None.

    None whenever the guess would be invented rather than measured: before
    the first side, before any time has passed, or once the count has
    reached the document's end. A printer is watched by someone who can
    see the tray, so a wrong estimate is worse than none.
    """
    if done <= 0 or seconds <= 0 or done >= total:
        return None
    rate = done / seconds
    minutes = round((total - done) / rate / _SECONDS_PER_MINUTE)
    return f"~{max(1, minutes)}m left"


def _sides(count: int) -> str:
    """ "1 side", "105 sides" - the reader is counting paper, so the word
    has to agree with the number."""
    return f"{count} side" if count == 1 else f"{count} sides"


def queueing_line(sent: int, total: int) -> str:
    """CUPS's count, while it still has sides to hand over.

    Labeled for what it is. Called "Sent" against the document's total it
    looked like printing progress, so "94/105" read as nearly finished
    while the printer in fact had six minutes of paper left.
    """
    return f"Queueing: {min(sent, total)} of {_sides(total)}..."


def queued_line(total: int) -> str:
    """Replaces the queueing line once CUPS has handed everything over.

    It names the printer on purpose: from here the print queue is empty
    and the only thing still working is the printer itself.
    """
    return f"Queued: {_sides(total)} sent to the printer."


def printing_line(done: int, total: int, duplex: str, seconds: float) -> str:
    """The live line: sheets first, then sides, elapsed, and an estimate.

    Sheets lead because sheets are what stacks up in the tray where the
    reader can count them. The elapsed time earns its place by changing on
    every poll - at roughly eight seconds a side, a line that only moved
    with the page count sat still long enough to look hung.
    """
    sheets = sheets_for(total, duplex)
    done_sheets = min(sheets_for(done, duplex), sheets)
    line = (
        f"Printing: sheet {done_sheets} of {sheets}  "
        f"({_sides(min(done, total))})  {format_elapsed(seconds)}"
    )
    estimate = remaining(done, total, seconds)
    return f"{line}, {estimate}" if estimate else line


def printed_line(total: int, duplex: str) -> str:
    """The closing line, in the document's terms rather than the printer's."""
    sheets = sheets_for(total, duplex)
    sheets_word = "sheet" if sheets == 1 else "sheets"
    return f"Printed {_sides(total)} on {sheets} {sheets_word}."


def stall_lines(done: int, total: int, seconds: float) -> tuple[str, ...]:
    """The warning for a count that has stopped moving, as separate lines.

    Separate lines rather than one block so the caller owns the prefixes,
    and so none of the prose has to carry indentation of its own.

    This exists because of 2026-11-05, when the tray ran dry at side 37 of
    72 and newsprint then waited two hours and nine minutes in silence.
    Everything a person can easily look at said the run was healthy: the
    queue claimed to be printing, the printer called itself idle with no
    reasons and paper ready, and CUPS held the job in `processing`. The
    only honest signal was a send queue frozen at 130,160 bytes, which is
    not somewhere anybody should have to look. So the warning names the
    checks that would have gotten there in a minute instead.
    """
    return (
        (
            f"No change for {format_elapsed(seconds)} - stuck at "
            f"{min(done, total)} of {_sides(total)}."
        ),
        "The printer may have taken the job and then dropped it. Running",
        "out of paper does exactly that, and it leaves the connection open,",
        "so the queue, the printer and CUPS all go on reporting health.",
        "Check the tray, then `lpstat -p`. A count still frozen there means",
        "the job is gone, and cancelling it is the only way forward.",
        "Ctrl-C is safe: no mail has been touched yet.",
    )
