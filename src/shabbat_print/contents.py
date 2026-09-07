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
# Times is narrower than Charter/Georgia at the same point size, so a width
# estimate taken from PyMuPDF's "tiro" alone under-predicts how much room a
# subject actually needs once WeasyPrint sets it in the real body font -
# every entry then "fits" the estimate but several visibly wrap onto a
# second line in the rendered PDF, exactly what a two-line row is supposed
# to avoid. Verified against the acceptance run's real 17-newsletter packet:
# scoring against 75% of the estimated remaining width removes every
# wrapped row while still keeping short subjects ("Day Off",
# "Weaponized Interdependence") on the line.
_SUBJECT_FIT_SAFETY = 0.75


def _starting_cells(built: Sequence[Built], contents_cells: int) -> list[int]:
    """Each newsletter's first packet cell number, assuming the contents
    page occupies exactly `contents_cells` cells at the very front."""
    starts: list[int] = []
    offset = contents_cells
    for item in built:
        starts.append(offset + 1)
        offset += item.cells
    return starts


def _row_text(item: Built, remaining_width_pt: float, font_size_pt: float) -> str:
    name = byline(item.document.publication, item.document.author)
    candidate = f"{name}{_SUBJECT_SEPARATOR}{item.document.title}"
    fits = (
        pymupdf.get_text_length(candidate, fontname=_BODY_FONT, fontsize=font_size_pt)
        <= remaining_width_pt
    )
    return candidate if fits else name


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
    """
    newsletters_cells = sum(item.cells for item in built)
    assumed = 1
    for attempt in range(CONVERGENCE_CAP):
        starts = _starting_cells(built, assumed)
        total_cells = assumed + newsletters_cells
        document = _contents_document(built, starts, packet_date, total_cells, config)
        pdf = render_fn(document, config, out_dir=out_dir / f"attempt-{attempt}")
        actual = page_count(pdf)
        if actual == assumed:
            return (
                Built(document=document, pdf=pdf, cells=actual, verdict=Verdict.FULL),
                True,
            )
        assumed = actual
    return None, False
