"""Build the packet's contents page.

One line per newsletter, naming the cell it starts on. The self-reference
is the whole difficulty: the contents page's own numbers depend on how many
cells it occupies, but its own length depends on what it says, since a
longer table of contents needs more cells. That is resolved by iterating to
a fixed point (see build_contents), the same technique real typesetting
systems use for the same reason.

Rendered through render.py, onto cell-sized pages, exactly like a
newsletter - so it inherits the same typography, page box, and (once
stamp.py runs over it too, as just another entry in the packet) footer.
It is deliberately *not* run through trim.fit(): trim's filler/widow
judgment is tuned for newsletter prose - boilerplate.is_boilerplate_line
treats any line under 40 characters without sentence-ending punctuation as
chrome, which is exactly what a "12  The Economist · Espresso" row looks
like. Trimming the contents page the same way a newsletter is trimmed risks
classifying a sparsely-filled second page as filler and silently dropping
real entries.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time
from html import escape
from pathlib import Path

import pymupdf

from .config import Config
from .geometry import MM_PER_INCH, POINTS_PER_INCH
from .models import Document, Origin, Verdict
from .pdfutil import page_count
from .pipeline import Built
from .render import render
from .stamp import byline

CONVERGENCE_CAP = 3
_BODY_FONT = "tiro"  # PyMuPDF's built-in Times-like serif: the closest
# base-14 stand-in for render.py's Charter/Georgia body font.
_NUMBER_GAP_PT = 6.0
_SUBJECT_SEPARATOR = " — "  # em dash
_ELLIPSIS = "…"
# Times is narrower than Charter/Georgia at the same point size, so a width
# estimate taken from PyMuPDF's "tiro" alone under-predicts how much room a
# subject actually needs once WeasyPrint sets it in the real body font -
# text sized to exactly fill the estimate then visibly wraps onto a second
# line in the rendered PDF, exactly what a one-line row is supposed to
# avoid. Verified against the acceptance run's real 17-newsletter packet,
# and re-verified end to end (test_a_long_subject_never_wraps_the_row_in_a_
# real_render) once every row started carrying a subject rather than only
# the ones whose subject happened to fit outright: scoring against 75% of
# the estimated remaining width keeps every row - including a subject
# truncated to fill that budget exactly - on one line.
_SUBJECT_FIT_SAFETY = 0.75


def _starting_cells(
    built: Sequence[Built], contents_cells: int, summary_cells: int = 0
) -> list[int]:
    """Each newsletter's first packet cell number, assuming the contents
    page occupies exactly `contents_cells` cells at the very front, and the
    (already-generated, already fixed-length) summary pages occupy
    `summary_cells` cells right after it - see build_contents for why the
    summary is never part of this function's own fixed point."""
    starts: list[int] = []
    offset = contents_cells + summary_cells
    for item in built:
        starts.append(offset + 1)
        offset += item.cells
    return starts


def _text_width_pt(text: str, font_size_pt: float) -> float:
    return pymupdf.get_text_length(text, fontname=_BODY_FONT, fontsize=font_size_pt)


def _truncate_to_width(text: str, max_width_pt: float, font_size_pt: float) -> str:
    """Truncate text with a trailing ellipsis so it fits max_width_pt.

    Snapped back to the last full word that fits, not cut mid-word - "A
    Subject Line So…" reads as a real title clipped for space; "A Subject
    Line So Lon…" reads as a bug. Falls back to a mid-word cut only when
    not even one whole word fits in the budget.

    Returns the empty string if not even one character plus the ellipsis
    fits - the caller's cue to drop the subject entirely rather than
    render a bare ellipsis with nothing in front of it.
    """
    if _text_width_pt(text, font_size_pt) <= max_width_pt:
        return text
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + _ELLIPSIS
        if _text_width_pt(candidate, font_size_pt) <= max_width_pt:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    if best == _ELLIPSIS or not best:
        return ""
    fitted = best.removesuffix(_ELLIPSIS).rstrip()
    if " " in fitted:
        word_boundary = fitted.rsplit(" ", 1)[0]
        if word_boundary:
            return f"{word_boundary}{_ELLIPSIS}"
    return best


def _row_text(item: Built, remaining_width_pt: float, font_size_pt: float) -> str:
    """Byline, plus as much of the subject as fits on one line.

    The number column is separate and untouched by any of this - the
    starting-cell number a reader navigates by is never truncated. The
    subject is truncated with an ellipsis rather than dropped outright
    whenever the full "name — subject" does not fit, since a reader can
    still recognise a truncated headline but gets nothing at all from a
    bare byline; it is dropped only in the extreme case where not even
    one truncated character of it would fit next to the name.
    """
    name = byline(item.document.publication, item.document.author)
    full = f"{name}{_SUBJECT_SEPARATOR}{item.document.title}"
    if _text_width_pt(full, font_size_pt) <= remaining_width_pt:
        return full
    subject_budget_pt = remaining_width_pt - _text_width_pt(
        f"{name}{_SUBJECT_SEPARATOR}", font_size_pt
    )
    if subject_budget_pt <= 0:
        return name
    fitted_subject = _truncate_to_width(
        item.document.title, subject_budget_pt, font_size_pt
    )
    if not fitted_subject:
        return name
    return f"{name}{_SUBJECT_SEPARATOR}{fitted_subject}"


def _contents_document(
    built: Sequence[Built],
    starts: Sequence[int],
    packet_date: date,
    total_cells: int,
    config: Config,
) -> Document:
    layout = config.layout
    paper = config.printing.paper
    scale = POINTS_PER_INCH / MM_PER_INCH
    usable_width_pt = (paper.cell.width_mm - 2 * layout.margin_mm) * scale
    number_column_pt = (
        pymupdf.get_text_length(
            str(max(starts, default=0)),
            fontname=_BODY_FONT,
            fontsize=layout.font_size_pt,
        )
        + _NUMBER_GAP_PT
    )
    remaining_width_pt = (
        max(0.0, usable_width_pt - number_column_pt) * _SUBJECT_FIT_SAFETY
    )

    rows = [
        "<tr>"
        '<td style="text-align:right; padding-right:6pt; white-space:nowrap; '
        f'vertical-align:top;">{escape(str(start))}</td>'
        f"<td>{escape(_row_text(item, remaining_width_pt, layout.font_size_pt))}</td>"
        "</tr>"
        for start, item in zip(starts, built, strict=True)
    ]
    content = (
        '<table style="width:100%; border-collapse:collapse;"><tbody>'
        + "".join(rows)
        + "</tbody></table>"
    )
    count = len(built)
    return Document(
        origin=Origin(kind="url", identifier="contents"),
        publication="Contents",
        title=f"{count} newsletter{'' if count == 1 else 's'} · {total_cells} cells",
        date=datetime.combine(packet_date, time.min, tzinfo=UTC),
        html=content,
    )


def build_contents(
    built: Sequence[Built],
    config: Config,
    packet_date: date,
    out_dir: Path,
    render_fn: Callable[..., Path] = render,
    summary_cells: int = 0,
) -> tuple[Built | None, bool]:
    """Render the contents page, iterating to a fixed point on its own
    length.

    1. Assume the contents occupies `k` cells, starting at k = 1.
    2. Compute every newsletter's starting cell as if that were true.
    3. Render the contents with those numbers; count the cells it took.
    4. If that count differs from `k`, set k to the actual count and repeat.

    Capped at CONVERGENCE_CAP attempts. On success, returns the contents
    Built and True. If it never settles within the cap, returns (None,
    False) rather than ship numbers nobody can trust - a missing contents
    page is a smaller problem than a wrong one.

    summary_cells is the cell count of the generated summary pages
    (summarize.py), which sit between contents and the first newsletter.
    It is a plain addend, not a second fixed point: the summary is
    generated first and its length does not depend on any number contents
    prints, so there is nothing here to iterate on. It shifts every
    starting-cell number and the total exactly the way contents' own
    assumed length does - see _starting_cells.
    """
    newsletters_cells = sum(item.cells for item in built)
    assumed = 1
    for attempt in range(CONVERGENCE_CAP):
        starts = _starting_cells(built, assumed, summary_cells)
        total_cells = assumed + summary_cells + newsletters_cells
        document = _contents_document(built, starts, packet_date, total_cells, config)
        pdf = render_fn(
            document,
            config,
            out_dir=out_dir / f"attempt-{attempt}",
            packet_title=config.packet.title,
        )
        actual = page_count(pdf)
        if actual == assumed:
            return (
                Built(document=document, pdf=pdf, cells=actual, verdict=Verdict.FULL),
                True,
            )
        assumed = actual
    return None, False
