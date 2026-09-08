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


def test_truncate_to_width_returns_text_unchanged_when_it_already_fits() -> None:
    from shabbat_print.contents import _truncate_to_width

    assert _truncate_to_width("Short", max_width_pt=400.0, font_size_pt=9.0) == "Short"


def test_truncate_to_width_falls_back_to_a_mid_word_cut_with_no_space() -> None:
    """When not even the first word fits, there is no word boundary to
    snap back to - the truncation falls back to a bare character cut
    rather than losing the word (and its ellipsis) entirely."""
    from shabbat_print.contents import _truncate_to_width

    result = _truncate_to_width(
        "Supercalifragilisticexpialidocious", max_width_pt=8.0, font_size_pt=9.0
    )
    assert result == "S…"


def test_truncate_to_width_keeps_a_mid_word_cut_when_the_only_space_is_leading() -> (
    None
):
    """A pathological but real edge case: if the only space in the fitted
    text is a leading one (e.g. a subject with stray leading whitespace),
    snapping "back to the last word boundary" would leave nothing at all
    - the mid-word cut is kept instead of collapsing to an empty result."""
    from shabbat_print.contents import _truncate_to_width

    result = _truncate_to_width(" Extraordinarily", max_width_pt=10.0, font_size_pt=9.0)
    assert result == " E…"


def test_row_text_drops_the_subject_when_the_budget_is_too_small_to_fit_any(
    config, tmp_path: Path
) -> None:
    """A budget that is positive but smaller than even one truncated
    character plus the ellipsis must still fall back to the byline alone,
    not a bare dangling ellipsis."""
    from dataclasses import replace

    from shabbat_print.contents import _row_text
    from shabbat_print.stamp import byline

    built = _built(0, publication="Money Stuff")
    name = byline(built.document.publication, built.document.author)
    from shabbat_print.contents import _text_width_pt

    # Just enough room for the byline and separator, but not one more
    # character of a truncated subject.
    budget = _text_width_pt(f"{name} — ", font_size_pt=9.0) + 2.0
    built = replace(
        built, document=replace(built.document, title="Supercalifragilistic")
    )
    text = _row_text(built, remaining_width_pt=budget, font_size_pt=9.0)
    assert text == name
    assert "…" not in text


def test_a_long_subject_is_truncated_with_an_ellipsis(config, tmp_path: Path) -> None:
    """The subject must always appear, truncated to fit one line - never
    wrapped to two, and never dropped just because the whole thing does
    not fit."""
    from dataclasses import replace

    from shabbat_print.contents import _row_text

    built = _built(0, publication="Money Stuff")
    built = replace(
        built,
        document=replace(
            built.document,
            title="Talking Interest Rates with Ricardo Caballero",
        ),
    )
    text = _row_text(built, remaining_width_pt=120.0, font_size_pt=9.0)
    assert built.document.title not in text  # the full title does not fit
    assert "Money Stuff" in text
    assert text.endswith("…")
    # A genuine prefix of the real title, not an unrelated truncation.
    subject_part = text.split(" — ", 1)[1]
    assert built.document.title.startswith(subject_part.removesuffix("…").rstrip())


def test_an_extremely_narrow_budget_drops_the_subject_entirely(
    config, tmp_path: Path
) -> None:
    """If there is no room for even one truncated character, the subject
    is dropped rather than rendered as a bare ellipsis dangling off the
    byline - the number's own column is never touched either way."""
    from shabbat_print.contents import _row_text
    from shabbat_print.stamp import byline

    built = [_built(0, publication="Money Stuff")][0]
    text = _row_text(built, remaining_width_pt=1.0, font_size_pt=9.0)
    assert built.document.title not in text
    assert "…" not in text
    assert text == byline(built.document.publication, built.document.author)


def test_a_document_whose_subject_does_not_fully_fit_is_truncated_not_dropped(
    config, tmp_path: Path
) -> None:
    """End-to-end: a genuinely long subject line must not appear verbatim
    in the rendered contents page, but a truncated, ellipsis-terminated
    prefix of it must still appear - the byline alone is no longer
    considered an acceptable substitute."""
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
    # A genuine, word-boundary-truncated prefix of the real subject.
    assert "A Subject Line So" in text
    assert "…" in text


def test_a_long_subject_never_wraps_the_row_in_a_real_render(
    config, tmp_path: Path
) -> None:
    """Verify against the actual rendered PDF, not the width estimate: a
    row with a long, realistic subject must occupy exactly one text line,
    the same as a row with no subject at all - never two."""
    built = [
        _built(0, publication="Paul Krugman"),
        _built(1, publication="William D. Cohan"),
    ]
    from dataclasses import replace

    built[0] = replace(
        built[0],
        document=replace(
            built[0].document,
            title="Talking Interest Rates with Ricardo Caballero, and Whether "
            "U.S. Bonds Are Still the Ultimate Safe Asset in a Turbulent Year",
        ),
    )
    built[1] = replace(
        built[1],
        document=replace(built[1].document, title="WarnerMount Arb Smoke Signals"),
    )
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    text = page_text(result.pdf, 0)
    lines = [line for line in text.splitlines() if line.strip()]
    # Header (masthead-equivalent line) + summary line + one line per
    # newsletter - never more, which is what a wrapped row would add.
    assert len(lines) == 2 + len(built)


def test_the_packet_title_appears_on_the_contents_page(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[packet]\ntitle = "Family Shabbat Reading"\n')
    config = load_config(path)
    built = [_built(0), _built(1)]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    text = page_text(result.pdf, 0)
    assert "family shabbat reading" in text.lower()


def test_an_empty_packet_title_renders_nothing_on_the_contents_page(
    config, tmp_path: Path
) -> None:
    """The tracked default is empty, so a run with no config carries no
    title line at all - the same page as before H1."""
    built = [_built(0), _built(1)]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    assert config.packet.title == ""


def test_summary_cells_shift_the_starting_numbers_too(config, tmp_path: Path) -> None:
    """summary_cells stands in for pages (the generated summary) that sit
    between contents and the first newsletter - contents must count them
    into every starting-cell number and into its own total, without
    running a second fixed point over them."""
    built = [_built(0, cells=3), _built(1, cells=3)]
    result, converged = build_contents(
        built, config, PACKET_DATE, tmp_path, summary_cells=2
    )
    assert converged
    assert result is not None
    assert result.cells == 1
    text = page_text(result.pdf, 0)
    starts = re.findall(r"^(\d+)\s+Newsletter", text, re.MULTILINE)
    # 1 (contents) + 2 (summary) = 3, so the first newsletter starts at 4.
    assert starts == ["4", "7"]
    assert "9 cells" in text  # 1 contents + 2 summary + 6 newsletter cells


def test_summary_cells_default_to_zero_and_change_nothing(
    config, tmp_path: Path
) -> None:
    """With the feature disabled, summary_cells is never passed - the
    default of 0 must reproduce today's numbers exactly."""
    built = [_built(0, cells=3), _built(1, cells=3)]
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    text = page_text(result.pdf, 0)
    starts = re.findall(r"^(\d+)\s+Newsletter", text, re.MULTILINE)
    assert starts == ["2", "5"]


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
