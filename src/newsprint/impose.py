"""Tile cell-sized pages four to a sheet side.

Because a cell is exactly a quarter of a sheet, the cells are placed at 100%
scale: nothing is resampled and no margin is lost to an aspect mismatch.

Duplex needs no special ordering here. Output page 1 is the front of sheet 1
and carries cells 1-4; output page 2 is its back and carries cells 5-8. The
printer's long-edge duplex does the rest.
"""

import contextlib
from collections.abc import Sequence
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation

from .geometry import Paper


def _offsets(paper: Paper) -> tuple[tuple[float, float], ...]:
    """Where each cell's bottom-left corner goes on the sheet.

    The PDF origin is bottom-left, so the top row sits one cell height
    up. Reading order is across then down, which for two a side is simply
    top then bottom - there is nothing beside a cell that spans the
    sheet.
    """
    cell_width, cell_height = paper.cell.as_points()
    across = paper.cells_per_side // 2
    return tuple(
        (column * cell_width, cell_height if row == 0 else 0.0)
        for row in range(2)
        for column in range(across)
    )


def impose(cell_pdfs: Sequence[Path], paper: Paper, out: Path) -> int:
    """Write the imposed document and return how many sheet sides it has.

    One PdfReader per document is opened, each holding its own file
    descriptor. ExitStack guarantees they are all closed once this function
    returns - deterministically, rather than whenever the garbage collector
    gets around to it - which matters on a run building many newsletters at
    once, against a process-wide fd limit.
    """
    with contextlib.ExitStack() as stack:
        readers = [stack.enter_context(PdfReader(str(path))) for path in cell_pdfs]
        cells = [page for reader in readers for page in reader.pages]
        if not cells:
            raise ValueError("nothing to impose")

        sheet_width, sheet_height = paper.sheet.as_points()
        offsets = _offsets(paper)
        per_side = len(offsets)

        writer = PdfWriter()
        for start in range(0, len(cells), per_side):
            side = PageObject.create_blank_page(width=sheet_width, height=sheet_height)
            for cell, (dx, dy) in zip(cells[start : start + per_side], offsets):
                side.merge_transformed_page(cell, Transformation().translate(dx, dy))
            writer.add_page(side)

        with out.open("wb") as handle:
            writer.write(handle)
    return len(writer.pages)
