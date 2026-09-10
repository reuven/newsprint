import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pymupdf
import pytest

from newsprint.config import load_config
from newsprint.contents import build_contents
from newsprint.geometry import MM_PER_INCH, POINTS_PER_INCH
from newsprint.models import Document, Origin, Verdict
from newsprint.pdfutil import page_count, page_text
from newsprint.pipeline import Built
from newsprint.stamp import byline

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
    # so match on the capitalized "Newsletter NN" row content specifically
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
    from newsprint.contents import _row_text

    built = [_built(0, publication="Money Stuff")][0]
    text = _row_text(built, remaining_width_pt=400.0, font_size_pt=9.0)
    assert built.document.title in text
    assert "Money Stuff" in text


def test_truncate_to_width_returns_text_unchanged_when_it_already_fits() -> None:
    from newsprint.contents import _truncate_to_width

    assert _truncate_to_width("Short", max_width_pt=400.0, font_size_pt=9.0) == "Short"


def test_truncate_to_width_falls_back_to_a_mid_word_cut_with_no_space() -> None:
    """When not even the first word fits, there is no word boundary to
    snap back to - the truncation falls back to a bare character cut
    rather than losing the word (and its ellipsis) entirely."""
    from newsprint.contents import _truncate_to_width

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
    from newsprint.contents import _truncate_to_width

    result = _truncate_to_width(" Extraordinarily", max_width_pt=10.0, font_size_pt=9.0)
    assert result == " E…"


def test_row_text_drops_the_subject_when_the_budget_is_too_small_to_fit_any(
    config, tmp_path: Path
) -> None:
    """A budget that is positive but smaller than even one truncated
    character plus the ellipsis must still fall back to the byline alone,
    not a bare dangling ellipsis."""
    from dataclasses import replace

    from newsprint.contents import _row_text
    from newsprint.stamp import byline

    built = _built(0, publication="Money Stuff")
    name = byline(built.document.publication, built.document.author)
    from newsprint.contents import _text_width_pt

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

    from newsprint.contents import _row_text

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
    from newsprint.contents import _row_text
    from newsprint.stamp import byline

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


def test_a_byline_that_is_nearly_a_duplicate_author_never_wraps_the_row(
    config, tmp_path: Path
) -> None:
    """Real packet data: publication and author overlap so heavily -
    "Ruth Ben-Ghiat from Lucid" byline "Ruth Ben-Ghiat and Joyce Vance
    from Lucid" - that they used to fail the old contains-check duplicate
    suppression, produce an un-truncated double-width byline, and wrap
    onto a second line. Both the widened duplicate suppression and the
    byline-truncation backstop must prevent that end to end."""
    from dataclasses import replace

    built = [_built(0, publication="Ruth Ben-Ghiat from Lucid")]
    built[0] = replace(
        built[0],
        document=replace(
            built[0].document,
            author="Ruth Ben-Ghiat and Joyce Vance from Lucid",
        ),
    )
    result, converged = build_contents(built, config, PACKET_DATE, tmp_path)
    assert converged
    assert result is not None
    text = page_text(result.pdf, 0)
    lines = [line for line in text.splitlines() if line.strip()]
    # Header + summary line + one line for the single newsletter - never
    # two, which is what a wrapped row would add.
    assert len(lines) == 2 + len(built)


@pytest.mark.parametrize(
    "publication,author,title",
    [
        ("Extraordinarily Long Publication Name " * 4, "Author 0", "Issue 0"),
        ("Money Stuff", "An Extraordinarily Long Author Name " * 4, "Issue 0"),
        (
            "Extraordinarily Long Publication Name " * 4,
            "An Extraordinarily Long Author Name " * 4,
            "Issue 0",
        ),
        (
            "Money Stuff",
            "Author 0",
            "A Wildly Long Subject Line That Cannot Possibly Fit " * 4,
        ),
    ],
    ids=["long-publication", "long-author", "both-long", "long-subject"],
)
def test_no_row_text_ever_exceeds_the_usable_width(
    publication: str, author: str, title: str
) -> None:
    """_row_text is the single choke point every contents row passes
    through - if its output never exceeds remaining_width_pt, no row can
    wrap, regardless of how pathological the publication, author, or
    subject are individually or in combination."""
    from dataclasses import replace

    from newsprint.contents import _row_text, _text_width_pt

    built = _built(0, publication=publication)
    built = replace(
        built,
        document=replace(built.document, author=author, title=title),
    )
    remaining_width_pt = 200.0
    text = _row_text(built, remaining_width_pt=remaining_width_pt, font_size_pt=9.0)
    assert _text_width_pt(text, font_size_pt=9.0) <= remaining_width_pt


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


# ---------------------------------------------------------------------------
# _truncate_to_width. The contents page is where the user noticed subjects
# being cut inconsistently, and mutation testing found the boundaries and
# the word-boundary snap were all untested: 45 survivors in this module.
# ---------------------------------------------------------------------------

LONG_SUBJECT = "A Subject Line So Long It Cannot Possibly Fit"


def test_truncation_snaps_back_to_a_whole_word() -> None:
    """ "A Subject Line So…" reads as a title clipped for space; "A Subject
    Line So Lon…" reads as a bug. Nothing tested the snap, so a rule that
    never snapped passed."""
    from newsprint.contents import _truncate_to_width

    assert _truncate_to_width(LONG_SUBJECT, 120.0, 9.0) == "A Subject Line So Long It…"
    assert _truncate_to_width(LONG_SUBJECT, 60.0, 9.0) == "A Subject…"


def test_truncation_falls_back_to_a_mid_word_cut() -> None:
    """When not even one whole word fits, a mid-word cut beats nothing."""
    from newsprint.contents import _truncate_to_width

    assert _truncate_to_width(LONG_SUBJECT, 30.0, 9.0) == "A…"


def test_text_that_exactly_fills_the_width_is_not_truncated() -> None:
    """The fit test is <=, not <."""
    from newsprint.contents import _text_width_pt, _truncate_to_width

    exact = _text_width_pt("Money Stuff", 9.0)
    assert _truncate_to_width("Money Stuff", exact, 9.0) == "Money Stuff"
    assert _truncate_to_width("Money Stuff", exact - 1.0, 9.0) != "Money Stuff"


def test_a_width_too_narrow_for_anything_yields_nothing() -> None:
    """Not a bare ellipsis with no text in front of it: the caller reads
    the empty string as "drop the subject entirely"."""
    from newsprint.contents import _truncate_to_width

    assert _truncate_to_width(LONG_SUBJECT, 6.0, 9.0) == ""


def test_the_trailing_space_goes_not_the_leading_one() -> None:
    """The snap leaves the space that preceded the last word; it comes off
    the end. Stripping the other end would leave "A Subject Line So …"."""
    from newsprint.contents import _truncate_to_width

    result = _truncate_to_width(LONG_SUBJECT, 120.0, 9.0)
    assert not result.startswith(" ")
    assert " …" not in result


def test_one_newsletter_is_singular_in_the_header(config, tmp_path: Path) -> None:
    """ "1 newsletters · 3 cells" is the kind of thing a reader notices
    every single week. Nothing asserted the plural, so a rule that always
    said "s" passed."""
    contents, _converged = build_contents([_built(0)], config, PACKET_DATE, tmp_path)
    assert contents is not None
    assert "1 newsletter ·" in contents.document.title
    assert "newsletters" not in contents.document.title


def test_several_newsletters_are_plural_in_the_header(config, tmp_path: Path) -> None:
    contents, _converged = build_contents(
        [_built(0), _built(1)], config, PACKET_DATE, tmp_path
    )
    assert contents is not None
    assert "2 newsletters ·" in contents.document.title


def test_the_contents_page_is_bylined_contents(config, tmp_path: Path) -> None:
    """stamp.py prints document.publication in every cell's footer, so
    this string is on the printed page, not just in a structure."""
    contents, _converged = build_contents([_built(0)], config, PACKET_DATE, tmp_path)
    assert contents is not None
    assert contents.document.publication == "Contents"


def test_the_contents_page_date_is_timezone_aware(config, tmp_path: Path) -> None:
    """Every other Document.date is UTC-aware (extract._date guarantees
    it), and cli.py sorts the packet by date - a naive one among aware
    ones raises TypeError rather than sorting wrongly."""
    contents, _converged = build_contents([_built(0)], config, PACKET_DATE, tmp_path)
    assert contents is not None
    assert contents.document.date.tzinfo is not None
    assert contents.document.date.utcoffset() == timedelta(0)


def test_the_contents_page_is_a_full_cell_not_a_verdict_of_none(
    config, tmp_path: Path
) -> None:
    """trim.py's verdict is what the run report reads; the contents page
    is never trimmed, so it is FULL by construction."""
    contents, _converged = build_contents([_built(0)], config, PACKET_DATE, tmp_path)
    assert contents is not None
    assert contents.verdict is Verdict.FULL


def test_a_starts_and_built_length_mismatch_is_an_error_not_a_silent_drop(
    monkeypatch, config, tmp_path: Path
) -> None:
    """zip(..., strict=True) guards an internal invariant: one starting
    cell per newsletter. Relaxing it drops the tail of the contents page
    silently, which is exactly the failure a reader would never spot."""
    from newsprint import contents as contents_module

    monkeypatch.setattr(contents_module, "_starting_cells", lambda *args, **kwargs: [1])
    with pytest.raises(ValueError):
        build_contents([_built(0), _built(1)], config, PACKET_DATE, tmp_path)


# ---------------------------------------------------------------------------
# The width a row's text is given, worked out from the constants rather
# than taken on trust. Every slip in it shows up the same way - a subject
# cut shorter or longer than the page can actually hold - and only a
# fixture sitting on the boundary can tell the difference.
# ---------------------------------------------------------------------------


def _expected_remaining_width(config, starts) -> float:
    from newsprint.contents import (
        _NUMBER_GAP_PT,
        _SUBJECT_FIT_SAFETY,
        _text_width_pt,
    )

    scale = POINTS_PER_INCH / MM_PER_INCH
    cell = config.printing.paper.cell
    usable = (cell.width_mm - 2 * config.layout.margin_mm) * scale
    number_column = (
        _text_width_pt(str(max(starts, default=0)), config.layout.font_size_pt)
        + _NUMBER_GAP_PT
    )
    return max(0.0, usable - number_column) * _SUBJECT_FIT_SAFETY


def test_a_row_is_given_the_page_less_its_number_column(config, tmp_path) -> None:
    """The row's text gets what is left of the cell after the margins and
    the column the largest starting number needs, less a gap so the two
    never touch - and then three quarters of that, since the font this is
    measured in is not the font the page is set in.

    The fixture is built against that width rather than compared to it: a
    subject grown until one more character would not fit comes through
    whole, and that same subject with one more character is cut. Any slip
    in the arithmetic moves the boundary and one of the two flips.
    """
    from dataclasses import replace

    from newsprint.contents import _contents_document, _row_text

    starts = [2]
    remaining = _expected_remaining_width(config, starts)
    font_size = config.layout.font_size_pt

    def row_for(subject: str) -> str:
        item = replace(_built(1), document=replace(_built(1).document, title=subject))
        return _contents_document([item], starts, PACKET_DATE, 5, config).html

    # Grow the subject to the last width that still fits whole.
    subject = "Wide"
    while "…" not in _row_text(
        replace(_built(1), document=replace(_built(1).document, title=subject + "W")),
        remaining_width_pt=remaining,
        font_size_pt=font_size,
    ):
        subject += "W"

    assert subject in row_for(subject), "the last subject that fits must survive whole"
    assert subject + "W" not in row_for(subject + "W"), "one more must be cut"
    assert "…" in row_for(subject + "W")


def test_the_contents_page_is_identified_as_the_contents(config) -> None:
    """render() names its file from the identifier, and the contents page
    is not mail - it has no Message-ID to fall back on, so it carries one
    of its own."""
    from newsprint.contents import _contents_document

    document = _contents_document([_built(1)], [2], PACKET_DATE, 4, config)
    assert document.origin.kind == "url"
    assert document.origin.identifier == "contents"


def test_starting_cells_count_the_contents_and_the_summary_ahead_of_it() -> None:
    """The number beside each newsletter is the cell it starts on, and
    everything printed before it pushes it along: the contents page itself
    first, then the summary if there is one."""
    from newsprint.contents import _starting_cells

    built = [_built(1, cells=3), _built(2, cells=2), _built(3, cells=4)]
    assert _starting_cells(built, contents_cells=1) == [2, 5, 7]
    assert _starting_cells(built, contents_cells=1, summary_cells=2) == [4, 7, 9]


def test_a_row_that_exactly_fills_its_width_keeps_its_subject() -> None:
    """Exactly the width available is a fit, not an overflow - the row
    that just reaches the edge is printed whole rather than clipped for
    the sake of a fraction of a point."""
    from newsprint.contents import _row_text, _text_width_pt

    item = _built(1)
    full = _row_text(item, remaining_width_pt=10_000.0, font_size_pt=9.0)
    exact = _text_width_pt(full, 9.0)
    assert _row_text(item, remaining_width_pt=exact, font_size_pt=9.0) == full
    assert _row_text(item, remaining_width_pt=exact - 1.0, font_size_pt=9.0) != full


def test_a_subject_budget_of_nothing_at_all_drops_the_subject() -> None:
    """When the byline alone fills the row there is no budget left, and
    zero is not a budget: asking to fit a subject into it would spend the
    work only to be told nothing fits."""
    from dataclasses import replace

    from newsprint.contents import _SUBJECT_SEPARATOR, _row_text, _text_width_pt

    item = _built(1)
    name_and_separator = _text_width_pt(
        f"{byline(item.document.publication, item.document.author)}"
        f"{_SUBJECT_SEPARATOR}",
        9.0,
    )
    long_titled = replace(
        item, document=replace(item.document, title="A subject with real words in it")
    )
    at_zero = _row_text(
        long_titled, remaining_width_pt=name_and_separator, font_size_pt=9.0
    )
    assert _SUBJECT_SEPARATOR not in at_zero, "no separator with nothing after it"


def test_a_truncation_candidate_that_exactly_fits_is_taken() -> None:
    """The binary search keeps the widest candidate that fits, and a
    candidate landing exactly on the width fits - rejecting it would throw
    away a whole character on every row that happened to line up."""
    from newsprint.contents import _text_width_pt, _truncate_to_width

    # A single word, so the snap back to a word boundary never fires and
    # what the search chose is what comes out.
    text = "Alphabetical"
    exact = _text_width_pt("Al…", 9.0)
    assert _truncate_to_width(text, exact, 9.0) == "Al…"
    assert _truncate_to_width(text, exact - 0.5, 9.0) == "A…"
