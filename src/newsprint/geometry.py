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


# Always two cells across a sheet; the layouts differ in how many rows.
CELLS_ACROSS = 2


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
    stock: Size
    cells_per_side: int = 4

    @property
    def sheet(self) -> Size:
        """The sheet as printed, which is not always the paper as sold.

        Four a side prints portrait: two cells across, two down. Two a
        side prints landscape, two cells across and one down, so that
        each cell is portrait - the shape a page is - and the fold down
        the middle of the sheet gives an A5 booklet. Two a side on a
        portrait sheet would instead make each cell wide and short, which
        is an awkward thing to read however large the type.
        """
        if self.cells_per_side == 2:
            return Size(self.stock.height_mm, self.stock.width_mm)
        return self.stock

    @property
    def cell(self) -> Size:
        """One cell of the sheet, by however many go on a side.

        Four is two across and two down, so a cell is half the sheet each
        way. Two is one across and two down - full width, half height -
        which keeps the separating fold in the same place and makes the
        cell A5 for an A4 sheet rather than a tall half-column.

        A bigger cell is not by itself larger type. Measured: at the
        four-up default of 9pt a landscape A5 cell runs about 88
        characters to the line, past the 45-75 that reads comfortably,
        where an A6 cell at the same size gives 55. Two a side is the
        *room* for larger type; layout.font_size_pt is what spends it,
        and 12pt brings the measure back to 66 - which is why config.py
        defaults this layout to 12pt rather than 9pt.
        """
        if self.cells_per_side in (2, 4):
            sheet = self.sheet
            return Size(
                sheet.width_mm / CELLS_ACROSS,
                sheet.height_mm / (self.cells_per_side // 2),
            )
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
