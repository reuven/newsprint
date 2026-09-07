from datetime import UTC, datetime
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.geometry import A4
from shabbat_print.models import Document, Origin
from shabbat_print.pdfutil import page_count, page_text, text_extent_mm
from shabbat_print.render import render

PROSE = (
    "<p>The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes.</p>"
)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def document(html: str) -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication="Money Stuff",
        title="Private Credit Gets Complicated",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
    )


def test_page_is_exactly_one_cell(config, tmp_path: Path) -> None:
    import pymupdf

    pdf = render(document(PROSE), config, out_dir=tmp_path)
    with pymupdf.open(pdf) as opened:
        rect = opened[0].rect
    expected_width, expected_height = A4.cell.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)
    assert rect.height == pytest.approx(expected_height, abs=1.0)


def test_title_and_publication_appear(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    text = page_text(pdf, 0)
    assert "Private Credit Gets Complicated" in text
    assert "Money Stuff" in text.replace("\n", " ")


def test_content_appears(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    assert "Federal Reserve" in page_text(pdf, 0)


def test_long_document_spans_several_cells(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert page_count(pdf) > 1


def test_compression_never_increases_the_page_count(config, tmp_path: Path) -> None:
    long_document = document(PROSE * 40)
    loose = render(long_document, config, out_dir=tmp_path)
    tight = render(long_document, config, compression=0.98, out_dir=tmp_path)
    assert page_count(tight) <= page_count(loose)


def test_compression_writes_a_distinct_file(config, tmp_path: Path) -> None:
    doc = document(PROSE)
    assert render(doc, config, out_dir=tmp_path) != render(
        doc, config, compression=0.98, out_dir=tmp_path
    )


def test_letter_paper_gives_a_letter_cell(tmp_path: Path) -> None:
    import pymupdf

    from shabbat_print.geometry import LETTER

    config = load_config(tmp_path / "absent.toml", paper_override="letter")
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    with pymupdf.open(pdf) as opened:
        rect = opened[0].rect
    expected_width, _ = LETTER.cell.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)


def test_text_extent_of_a_short_page_is_small(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    assert text_extent_mm(pdf, 0) < A4.cell.height_mm / 2


def test_text_extent_of_a_full_page_is_large(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert text_extent_mm(pdf, 0) > A4.cell.height_mm / 2


def test_dollar_signs_in_content_survive(config, tmp_path: Path) -> None:
    """The template substitutes with string.Template; $ in the content must
    not be treated as a placeholder."""
    pdf = render(
        document("<p>It cost $500 and $unexpected trouble.</p>"),
        config,
        out_dir=tmp_path,
    )
    assert "$500" in page_text(pdf, 0)
