"""Paper and cell geometry.

A cell is one quarter of a sheet, arranged as a 2x2 grid. Because that halves
both dimensions at once, a cell always has the same aspect ratio as the sheet,
so `impose.py` can tile cells at 100% scale and nothing is ever resampled.
"""

from dataclasses import dataclass

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0


@dataclass(frozen=True, slots=True)
class Size:
    """A rectangle, held in millimetres."""

    width_mm: float
    height_mm: float

    @property
    def aspect(self) -> float:
        return self.width_mm / self.height_mm

    def as_points(self) -> tuple[float, float]:
        """PDF geometry is in points; this is the only conversion site."""
        scale = POINTS_PER_INCH / MM_PER_INCH
        return (self.width_mm * scale, self.height_mm * scale)


@dataclass(frozen=True, slots=True)
class Paper:
    """A sheet, and the cell derived from it.

    `cells_per_side` is carried here rather than passed around because
    every consumer - the renderer sizing its page, trim measuring the
    text column, the footer, the contents page - asks the same question
    of the same object, and none of them cares how the answer was
    reached.
    """

    name: str
    sheet: Size
    cells_per_side: int = 4

    @property
    def cell(self) -> Size:
        """One cell of the sheet, by however many go on a side.

        Four is two across and two down, so a cell is half the sheet each
        way. Two is one across and two down - full width, half height -
        which keeps the separating fold in the same place and makes the
        cell A5 for an A4 sheet rather than a tall half-column.

        A wider cell is not by itself larger type. Measured: at the
        default 9pt an A5 cell runs about 116 characters to the line,
        well past the 45-75 that reads comfortably, where an A6 cell at
        the same size gives 55. Two a side is the *room* for larger type;
        layout.font_size_pt is what spends it, and around 18pt brings the
        measure back to 61.
        """
        if self.cells_per_side == 4:
            return Size(self.sheet.width_mm / 2, self.sheet.height_mm / 2)
        if self.cells_per_side == 2:
            return Size(self.sheet.width_mm, self.sheet.height_mm / 2)
        raise ValueError(
            f"{self.cells_per_side} cells per side is not a layout this "
            "imposes; use 2 or 4"
        )


A4 = Paper("A4", Size(210.0, 297.0))
LETTER = Paper("Letter", Size(8.5 * MM_PER_INCH, 11.0 * MM_PER_INCH))

PAPERS: dict[str, Paper] = {"a4": A4, "letter": LETTER}


def paper_by_name(name: str) -> Paper:
    key = name.strip().lower()
    if key not in PAPERS:
        known = ", ".join(sorted(PAPERS))
        raise ValueError(f"unknown paper {name!r}; known papers are: {known}")
    return PAPERS[key]
