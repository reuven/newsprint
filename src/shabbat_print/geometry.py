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
    """A sheet, and the cell derived from it."""

    name: str
    sheet: Size

    @property
    def cell(self) -> Size:
        """One quarter of the sheet: half the width, half the height."""
        return Size(self.sheet.width_mm / 2, self.sheet.height_mm / 2)


A4 = Paper("A4", Size(210.0, 297.0))
LETTER = Paper("Letter", Size(8.5 * MM_PER_INCH, 11.0 * MM_PER_INCH))

PAPERS: dict[str, Paper] = {"a4": A4, "letter": LETTER}


def paper_by_name(name: str) -> Paper:
    key = name.strip().lower()
    if key not in PAPERS:
        known = ", ".join(sorted(PAPERS))
        raise ValueError(f"unknown paper {name!r}; known papers are: {known}")
    return PAPERS[key]
