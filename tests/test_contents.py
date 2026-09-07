import re
from datetime import UTC, date, datetime
from pathlib import Path

import pymupdf
import pytest

from shabbat_print.config import load_config
from shabbat_print.contents import build_contents
from shabbat_print.models import Document, Origin, Verdict
from shabbat_print.pdfutil import page_count, page_text
from shabbat_print.pipeline import Built

PACKET_DATE = date(2026, 9, 5)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def _built(index: int, cells: int = 3, publication: str | None = None) -> Built:
    name = publication or f"Newsletter {index:02d}"
    document = Document(
        origin=Origin(kind="email", identifier=f"<{index}@example.com>"),
        publication=name,
        title=f"Issue {index}",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<p>x</p>",
        author=f"Author {index}",
    )
    # A real, tiny, single-page placeholder PDF - build_contents never opens
    # a newsletter's own .pdf, only its .cells, but Built requires a path.
    return Built(
        document=document, pdf=Path("/dev/null"), cells=cells, verdict=Verdict.FULL
    )


def test_starting_cells_account_for_the_contents_own_length(
    config, tmp_path: Path
) -> None:
    """With a 1-cell contents, a 3-cell newsletter starts at 2 and the next
    at 5 - the worked example from the spec."""
    built = [_built(0, cells=3), _built(1, cells=3)]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    assert result.cells == 1
    text = page_text(result.pdf, 0)
    # The header line ("2 newsletters · 7 cells") also starts with a digit,
    # so match on the capitalised "Newsletter NN" row content specifically
    # rather than "starts with a digit" alone.
    starts = re.findall(r"^(\d+)\s+Newsletter", text, re.MULTILINE)
    assert starts == ["2", "5"]


def test_the_fixed_point_converges_when_contents_grows_to_two_cells(
    config, tmp_path: Path
) -> None:
    """Enough newsletters that the contents table itself overflows a
    single cell, forcing a second fixed-point attempt."""
    built = [_built(i) for i in range(25)]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    assert result.cells == 2
    assert page_count(result.pdf) == 2


def test_every_newsletter_appears_exactly_once(config, tmp_path: Path) -> None:
    built = [_built(i) for i in range(25)]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    full_text = "\n".join(page_text(result.pdf, i) for i in range(result.cells))
    for i in range(25):
        assert full_text.count(f"Newsletter {i:02d}") == 1


def test_a_single_newsletter_still_produces_a_sensible_contents_page(
    config, tmp_path: Path
) -> None:
    built = [_built(0, cells=5, publication="Solo Weekly")]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    assert result.cells == 1
    text = page_text(result.pdf, 0)
    assert "Solo Weekly" in text
    assert "1 newsletter" in text  # singular, not "1 newsletters"
    assert "6 cells" in text  # 1 (contents) + 5


def test_a_short_subject_is_included_on_the_line(config, tmp_path: Path) -> None:
    from shabbat_print.contents import _row_text

    built = [_built(0, publication="Money Stuff")][0]
    text = _row_text(built, remaining_width_pt=400.0, font_size_pt=9.0)
    assert built.document.title in text
    assert "Money Stuff" in text


def test_a_subject_that_does_not_fit_is_left_off_the_line(
    config, tmp_path: Path
) -> None:
    """At 4-up the width is the scarce resource: a subject that would not
    fit must be dropped rather than wrap the row onto a second line."""
    from shabbat_print.contents import _row_text

    built = [_built(0, publication="Money Stuff")][0]
    text = _row_text(built, remaining_width_pt=1.0, font_size_pt=9.0)
    assert built.document.title not in text
    assert "Money Stuff" in text


def test_a_document_whose_full_line_does_not_fit_appears_without_its_subject(
    config, tmp_path: Path
) -> None:
    """End-to-end: a genuinely long subject line must not appear verbatim
    in the rendered contents page - only the byline should."""
    long_title = "A Subject Line So Long It Cannot Possibly Fit Next To The Name " * 3
    built = [_built(0, cells=3), _built(1, cells=3)]
    document = built[1].document
    from dataclasses import replace

    built[1] = replace(built[1], document=replace(document, title=long_title))
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    text = page_text(result.pdf, 0)
    assert long_title not in text
    assert "Newsletter 01" in text


def test_non_convergence_within_the_cap_reports_rather_than_hangs(
    config, tmp_path: Path
) -> None:
    """A render_fn engineered to never agree with the assumption it was
    given: each attempt reports one more page than the last, so the
    fixed point never settles. Must stop after the cap, not loop forever."""
    built = [_built(0), _built(1)]
    calls: list[int] = []

    def never_settles(document, config, out_dir=None, **kwargs):
        call_index = len(calls)
        calls.append(call_index)
        pages = call_index + 2  # 2, 3, 4 - always one more than the last
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "contents.pdf"
        width, height = config.printing.paper.cell.as_points()
        with pymupdf.open() as pdf:
            for _ in range(pages):
                pdf.new_page(width=width, height=height)
            pdf.save(path)
        return path

    result, converged = build_contents(
        built, config, PACKET_DATE, tmp_path, render_fn=never_settles
    )
    assert result is None
    assert not converged
    assert len(calls) == 3  # capped, not unbounded
