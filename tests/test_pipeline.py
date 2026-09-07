from datetime import UTC, datetime
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.models import Document, Origin, Verdict
from shabbat_print.pipeline import build

PROSE = (
    "<p>The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes.</p>"
)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def document(html: str, identifier: str = "<a@example.com>") -> Document:
    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=1),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
    )


def test_builds_a_pdf_per_document(config, tmp_path: Path) -> None:
    built, failed = build(
        [document(PROSE, "<a@x>"), document(PROSE, "<b@x>")], config, tmp_path
    )
    assert len(built) == 2
    assert failed == []
    assert all(item.pdf.exists() for item in built)
    assert all(item.cells >= 1 for item in built)


def test_cells_matches_the_pdf_it_reports(config, tmp_path: Path) -> None:
    """Built.cells is what the sheet count is computed from, so it must equal
    the page count of the PDF actually handed on."""
    from shabbat_print.pdfutil import page_count

    built, _ = build([document(PROSE * 40)], config, tmp_path)
    assert built[0].cells == page_count(built[0].pdf)
    assert built[0].cells > 1
    assert isinstance(built[0].verdict, Verdict)


def test_a_failing_document_is_reported_not_raised(config, tmp_path: Path) -> None:
    broken = document(PROSE, "<c@x>")
    built, failed = build([broken], config, tmp_path, render_fn=_explode)
    assert built == []
    assert len(failed) == 1
    assert failed[0].document is broken
    assert "boom" in str(failed[0].error)


def _explode(*args, **kwargs):
    raise RuntimeError("boom")


def test_an_empty_document_is_a_failure_not_a_blank_page(
    config, tmp_path: Path
) -> None:
    """A newsletter that cleans down to nothing must not print an empty cell."""
    built, failed = build([document("")], config, tmp_path)
    assert built == []
    assert len(failed) == 1


def test_documents_sharing_an_identifier_do_not_collide(config, tmp_path: Path) -> None:
    """render() names its output from the document's identifier, and bulk mail
    without a Message-ID falls back to the sender address in extract.py, so
    two such messages can arrive with the same identifier. build() must not
    let one silently overwrite the other under a shared out_dir."""
    built, failed = build(
        [
            document(PROSE, "<same@example.com>"),
            document(PROSE, "<same@example.com>"),
        ],
        config,
        tmp_path,
    )
    assert failed == []
    assert len(built) == 2
    assert built[0].pdf != built[1].pdf
    assert built[0].pdf.exists()
    assert built[1].pdf.exists()
