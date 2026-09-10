import re
from datetime import UTC, date, datetime
from pathlib import Path

import pymupdf
import pytest

from newsprint.config import LayoutConfig
from newsprint.geometry import A4, MM_PER_INCH, POINTS_PER_INCH
from newsprint.models import Document, Origin, Verdict
from newsprint.pdfutil import page_text, text_extent_mm
from newsprint.pipeline import Built
from newsprint.stamp import byline, format_packet_date, stamp_packet

LAYOUT = LayoutConfig(margin_mm=9.0, font_size_pt=9.0, line_height=1.35)
PACKET_DATE = date(2026, 9, 5)


def _pdf(path: Path, pages: int) -> Path:
    """A blank multi-page PDF, sized like an A4 cell - what stamp_packet
    needs, since it stamps whatever pages a Built's .pdf already has."""
    width, height = A4.cell.as_points()
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=width, height=height)
            page.insert_text((20, 40), f"page {index + 1} body text", fontsize=9)
        document.save(path)
    return path


def _document(
    publication: str, author: str | None = None, title: str = "An Issue"
) -> Document:
    return Document(
        origin=Origin(kind="email", identifier=f"<{publication}@example.com>"),
        publication=publication,
        title=title,
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html="<p>content</p>",
        author=author,
    )


def _built(
    publication: str,
    pages: int,
    tmp_path: Path,
    name: str,
    author: str | None = None,
) -> Built:
    pdf = _pdf(tmp_path / f"{name}.pdf", pages)
    return Built(
        document=_document(publication, author=author),
        pdf=pdf,
        cells=pages,
        verdict=Verdict.FULL,
    )


def test_footer_segments_appear_with_correct_values(tmp_path: Path) -> None:
    built = [_built("Money Stuff", 1, tmp_path, "a", author="Matt Levine")]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    text = page_text(stamped[0], 0)
    assert "Money Stuff" in text
    assert "Matt Levine" in text
    assert "1/1" in text
    assert "1" in text
    assert "5 Sep 2026" in text


def test_packet_numbers_run_continuously_across_documents(tmp_path: Path) -> None:
    """The property CSS could not express: a 3-cell document followed by a
    2-cell one yields packet numbers 1,2,3 then 4,5."""
    built = [
        _built("First Weekly", 3, tmp_path, "a"),
        _built("Second Weekly", 2, tmp_path, "b"),
    ]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    first_texts = [page_text(stamped[0], i) for i in range(3)]
    second_texts = [page_text(stamped[1], i) for i in range(2)]

    for expected, text in zip([1, 2, 3], first_texts, strict=True):
        assert f"{expected} · 5 Sep 2026" in text
    for expected, text in zip([4, 5], second_texts, strict=True):
        assert f"{expected} · 5 Sep 2026" in text


def test_in_newsletter_numbers_restart_per_document(tmp_path: Path) -> None:
    built = [
        _built("First Weekly", 3, tmp_path, "a"),
        _built("Second Weekly", 2, tmp_path, "b"),
    ]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    first_texts = [page_text(stamped[0], i) for i in range(3)]
    second_texts = [page_text(stamped[1], i) for i in range(2)]

    assert "1/3" in first_texts[0]
    assert "2/3" in first_texts[1]
    assert "3/3" in first_texts[2]
    assert "1/2" in second_texts[0]
    assert "2/2" in second_texts[1]


@pytest.mark.parametrize(
    ("publication", "author"),
    [
        ("Axios Macro", "Axios Macro"),
        ("The Bulwark", "The Bulwark Podcast"),
        ("The Bulwark Podcast", "The Bulwark"),
        # Neither string contains the other, but they overlap so heavily -
        # the author repeats the publication's own name verbatim, with one
        # extra co-author inserted in the middle - that this is still one
        # person's byline, not two different names that happen to share a
        # word. Real packet data: contents.py's row for this newsletter
        # used to wrap onto a second line because of it.
        (
            "Ruth Ben-Ghiat from Lucid",
            "Ruth Ben-Ghiat and Joyce Vance from Lucid",
        ),
    ],
)
def test_author_omitted_when_it_duplicates_the_publication(
    tmp_path: Path, publication: str, author: str
) -> None:
    built = [_built(publication, 1, tmp_path, "a", author=author)]
    # stamp_packet must produce a stamped page at all, using this byline -
    # the direct assertion below is what proves the byline itself collapses,
    # since publication and author overlap heavily enough here that checking
    # the rendered text couldn't distinguish "included" from "duplicated".
    stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    assert byline(publication, author) == publication


def test_byline_combines_distinct_publication_and_author() -> None:
    assert byline("Money Stuff", "Matt Levine") == "Money Stuff · Matt Levine"


def test_byline_does_not_collapse_when_the_publication_has_no_word_characters() -> None:
    """Guards the word-overlap check's empty-set branch: a publication
    made entirely of punctuation has no words to compare, so it must
    neither crash (division by zero) nor be mistaken for a duplicate."""
    assert byline("!!!", "???") == "!!! · ???"


def test_byline_does_not_collapse_when_the_author_has_no_word_characters() -> None:
    """Same guard, the other argument: an author with no word characters
    at all must not crash or false-collapse a genuinely distinct
    publication."""
    assert byline("Money Stuff", "???") == "Money Stuff · ???"


def test_byline_keeps_a_shared_first_name_that_is_only_half_the_words() -> None:
    """Conservative boundary: exactly half of the shorter name's words
    overlapping is not "most" of them - a coincidental shared first name
    must not swallow a genuinely different author."""
    assert (
        byline("Weekly Notes by Jane", "Jane Smith")
        == "Weekly Notes by Jane · Jane Smith"
    )


def test_byline_with_no_author_is_bare_publication() -> None:
    assert byline("Money Stuff", None) == "Money Stuff"


def test_long_publication_name_truncates_while_numbers_stay_intact(
    tmp_path: Path,
) -> None:
    long_name = "The Extraordinarily Long Newsletter Name That Will Not Fit " * 3
    built = [_built(long_name, 1, tmp_path, "a", author="Some Author Name")]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    text = page_text(stamped[0], 0)
    assert "..." in text
    assert "1/1" in text
    assert "1 · 5 Sep 2026" in text
    # The numbers must not themselves be truncated.
    assert "5 Sep 2026" in text


def test_footer_baseline_is_inside_the_bottom_margin_band(tmp_path: Path) -> None:
    built = [_built("Money Stuff", 1, tmp_path, "a", author="Matt Levine")]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    scale = POINTS_PER_INCH / MM_PER_INCH
    margin_pt = LAYOUT.margin_mm * scale
    with pymupdf.open(stamped[0]) as document:
        page = document[0]
        footer_boundary = page.rect.height - margin_pt
        blocks = [b for b in page.get_text("blocks") if "5 Sep 2026" in b[4]]
    assert blocks
    # The footer block's top must sit at or below the boundary that
    # text_extent_mm uses to exclude footer content - i.e. strictly inside
    # the margin band, so it provably cannot overlap body text.
    assert blocks[0][1] >= footer_boundary


def test_stamped_footer_does_not_corrupt_text_extent(tmp_path: Path) -> None:
    """Stamping runs after trim, so the footer must not be mistaken for
    body content by text_extent_mm, which excludes anything in the margin
    band by position."""
    built = [_built("Money Stuff", 1, tmp_path, "a", author="Matt Levine")]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    extent = text_extent_mm(stamped[0], 0, LAYOUT.margin_mm)
    usable_mm = A4.cell.height_mm - 2 * LAYOUT.margin_mm
    assert extent < usable_mm  # the footer text itself is excluded


def test_date_uses_the_month_table_not_strftime(tmp_path: Path) -> None:
    """%b is locale-dependent (see mail._IMAP_MONTHS's docstring for the bug
    a Hebrew locale already caused once). format_packet_date must keep
    working the same way regardless of what the month table says, proving
    it is the source of truth rather than strftime."""
    import newsprint.stamp as stamp_module

    original = stamp_module._MONTHS
    try:
        stamp_module._MONTHS = ("Jan-x",) + original[1:]
        assert format_packet_date(date(2026, 1, 5)) == "5 Jan-x 2026"
    finally:
        stamp_module._MONTHS = original


def test_format_packet_date_matches_expected_english_abbreviation() -> None:
    assert format_packet_date(date(2026, 9, 5)) == "5 Sep 2026"


def test_truncate_of_a_short_string_is_unchanged() -> None:
    from newsprint.stamp import _truncate

    assert _truncate("Money Stuff", max_width_pt=200.0) == "Money Stuff"


def test_truncate_of_an_empty_string_is_unchanged() -> None:
    """Defensive: byline() never actually returns "", since Document.
    publication is always set - but _truncate must not crash if a future
    caller passes one, since nothing in its type signature rules it out."""
    from newsprint.stamp import _truncate

    assert _truncate("", max_width_pt=200.0) == ""


def test_truncate_with_no_room_even_for_the_ellipsis_yields_nothing() -> None:
    """A budget narrower than the ellipsis itself must not crash or return
    a lone, meaningless ellipsis - it degrades to nothing."""
    from newsprint.stamp import _truncate

    assert _truncate("Money Stuff", max_width_pt=0.5) == ""


def test_date_is_correct_under_a_non_english_locale() -> None:
    """The bug this guards against is real, not hypothetical: strftime('%b')
    under a Hebrew locale produced 'ספט׳' where IMAP - and now this footer -
    needs 'Sep' (see mail._IMAP_MONTHS). Switch LC_TIME for real and prove
    format_packet_date is unaffected, because it never calls strftime('%b')
    at all."""
    import locale

    original = locale.setlocale(locale.LC_TIME)
    try:
        locale.setlocale(locale.LC_TIME, "he_IL.UTF-8")
    except locale.Error:
        pytest.skip("he_IL.UTF-8 locale not installed on this machine")
    try:
        # Prove the locale switch actually took effect and would have
        # broken a strftime('%b')-based implementation.
        assert date(2026, 9, 5).strftime("%b") != "Sep"
        assert format_packet_date(date(2026, 9, 5)) == "5 Sep 2026"
    finally:
        locale.setlocale(locale.LC_TIME, original)


def test_draw_footer_skips_a_segment_that_truncates_to_nothing() -> None:
    """When the left segment has no room at all, _draw_footer must not
    insert an empty string - only skip it, leaving the numbers alone."""
    import pymupdf

    from newsprint.stamp import _draw_footer

    width, height = A4.cell.as_points()
    with pymupdf.open() as document:
        page = document.new_page(width=width, height=height)
        _draw_footer(page, A4, LAYOUT, left="", counter="1/1", right="1 · 5 Sep 2026")
        text = page.get_text()
    assert "1/1" in text
    assert "1 · 5 Sep 2026" in text


def test_the_footer_left_segment_carries_the_subject_after_the_byline() -> None:
    """A packet with seven issues of The Morning had seven identical
    footers; the subject is what tells them apart."""
    from newsprint.stamp import footer_left

    assert (
        footer_left("Platformer", "Casey Newton", "The AI warnings are coming")
        == "Platformer · Casey Newton · The AI warnings are coming"
    )


def test_the_byline_still_collapses_before_the_subject_is_added() -> None:
    from newsprint.stamp import footer_left

    assert (
        footer_left("Axios Macro", "Axios Macro", "New trade stakes")
        == "Axios Macro · New trade stakes"
    )


def test_a_subject_with_an_embedded_newline_is_flattened() -> None:
    """Real subjects in the user's own queue carry embedded CRLFs from
    unfolded header continuations."""
    from newsprint.stamp import footer_left

    assert (
        footer_left("Bite Code!", None, "clearly\r\n explained!")
        == "Bite Code! · clearly explained!"
    )


def test_an_empty_subject_leaves_the_byline_alone() -> None:
    from newsprint.stamp import footer_left

    assert footer_left("Platformer", "Casey Newton", "   ") == (
        "Platformer · Casey Newton"
    )


def test_a_subject_repeating_its_publication_drops_the_repeat() -> None:
    """A packet of seven Morning issues paid for "The Morning" twice on
    every one of them."""
    from newsprint.stamp import footer_left

    assert (
        footer_left("The Morning", None, "The Morning: The word is bond")
        == "The Morning · The word is bond"
    )
    assert (
        footer_left("Data Elixir", None, "Data Elixir - Issue 572")
        == "Data Elixir · Issue 572"
    )


def test_a_prefix_that_is_not_the_publication_is_kept() -> None:
    """ "Axios AM" under the byline "Mike Allen" is the only thing naming
    that newsletter - removing it would lose information, not repeat it."""
    from newsprint.stamp import footer_left

    assert (
        footer_left("Mike Allen", "Mike Allen", "Axios AM: Blue wave rising")
        == "Mike Allen · Axios AM: Blue wave rising"
    )


def test_a_subject_that_is_only_the_publication_name_survives() -> None:
    from newsprint.stamp import footer_left

    assert footer_left("Platformer", None, "Platformer: ") == "Platformer · Platformer:"


# ---------------------------------------------------------------------------
# Boundary and logic cases found by mutation testing.
# ---------------------------------------------------------------------------


def test_truncation_removes_one_character_at_a_time() -> None:
    """The loop walks back a character at a time. Replacing
    `truncated[:-1]` with `truncated[:1]` cuts straight to a single
    letter, and every existing test still passed - the footer would have
    read "P..." instead of most of the subject.
    """
    from newsprint.stamp import _truncate

    text = "Platformer · Casey Newton · The AI warnings are coming from inside the lab"
    assert _truncate(text, 100) == "Platformer · Casey Newton · The AI..."


def test_text_that_exactly_fills_the_footer_segment_is_left_alone() -> None:
    """The fit test is <=, not <: text exactly as wide as the segment
    fits, and truncating it would cost characters for no reason."""
    import pymupdf

    from newsprint.stamp import FONT, FONT_SIZE_PT, _truncate

    text = "Platformer"
    exact = pymupdf.get_text_length(text, fontname=FONT, fontsize=FONT_SIZE_PT)
    assert _truncate(text, exact) == text
    assert _truncate(text, exact - 1.0) != text


def test_a_segment_no_wider_than_the_ellipsis_yields_nothing() -> None:
    """Below the ellipsis's own width there is no honest way to show that
    text was cut, so the segment is dropped rather than showing a bare
    ellipsis where a byline should be."""
    import pymupdf

    from newsprint.stamp import ELLIPSIS, FONT, FONT_SIZE_PT, _truncate

    ellipsis_width = pymupdf.get_text_length(
        ELLIPSIS, fontname=FONT, fontsize=FONT_SIZE_PT
    )
    assert _truncate("Platformer · Casey Newton", ellipsis_width) == ""


def test_the_byline_collapses_whichever_name_contains_the_other() -> None:
    """Either direction counts, so the test is `or`, not `and`: the
    author can be the longer name ("The Bulwark" / "The Bulwark
    Podcast") or the shorter one."""
    assert byline("The Bulwark", "The Bulwark Podcast") == "The Bulwark"
    assert byline("Axios Macro Daily", "Axios Macro") == "Axios Macro Daily"
    assert byline("Puck", "Jon Kelly") == "Puck · Jon Kelly"


def test_a_repeated_publication_is_dropped_in_either_direction() -> None:
    """Same `or`, same reason: the subject's prefix can be longer than
    the publication ("Data Engineer Things Newsletter") or shorter."""
    from newsprint.stamp import footer_left

    assert (
        footer_left(
            "Data Engineer Things", None, "Data Engineer Things Newsletter - Data Pulse"
        )
        == "Data Engineer Things · Data Pulse"
    )
    assert (
        footer_left("The Morning Edition", None, "The Morning: The word is bond")
        == "The Morning Edition · The word is bond"
    )


# ---------------------------------------------------------------------------
# stamp_packet's own bookkeeping.
# ---------------------------------------------------------------------------


def test_the_output_directory_is_made_including_its_parents(tmp_path: Path) -> None:
    """A run writes its stamped pages under a per-run directory inside a
    working directory that may itself be new. Making only the last
    component fails on the first run of the day."""
    out_dir = tmp_path / "work" / "2026-09-05" / "stamped"
    stamped = stamp_packet(
        [_built("Money Stuff", 1, tmp_path, "a")], PACKET_DATE, A4, LAYOUT, out_dir
    )
    assert stamped[0].exists()


def test_stamping_twice_into_the_same_directory_is_not_an_error(
    tmp_path: Path,
) -> None:
    """Re-running after a failed print is ordinary, and the directory is
    then already there."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "leftover.txt").write_text("from an earlier run")
    stamped = stamp_packet(
        [_built("Money Stuff", 1, tmp_path, "a")], PACKET_DATE, A4, LAYOUT, out_dir
    )
    assert stamped[0].exists()


def test_the_running_page_number_accumulates_across_issues(tmp_path: Path) -> None:
    """The right-hand number is the sheet's place in the whole packet, not
    in its own issue - it is what the reader counts through on Shabbat.
    Three issues of two pages each run 1 to 6, so the third issue starts
    at 5: an offset that is merely set to the last issue's length rather
    than added to would start it at 3, and two issues would print with
    the same numbers on them.
    """
    built = [
        _built("Money Stuff", 2, tmp_path, "a"),
        _built("The Morning", 2, tmp_path, "b"),
        _built("Platformer", 2, tmp_path, "c"),
    ]
    stamped = stamp_packet(built, PACKET_DATE, A4, LAYOUT, tmp_path / "out")
    running = re.compile(r"^(\d+) \u00b7 5 Sep 2026$", re.MULTILINE)
    numbers = [
        running.search(page_text(path, index))[1]  # type: ignore[index]
        for path in stamped
        for index in range(2)
    ]
    assert numbers == ["1", "2", "3", "4", "5", "6"]


# ---------------------------------------------------------------------------
# Where the three footer segments actually land. Nothing here reads the
# geometry back out of the constants: each expected position is spelled
# out, so an arithmetic slip has somewhere to show.
# ---------------------------------------------------------------------------


def _footer_spans(left: str, counter: str, right: str) -> dict[str, dict[str, float]]:
    """Draw one footer and return each segment's x, baseline and colour."""
    import pymupdf

    from newsprint.stamp import _draw_footer

    width, height = A4.cell.as_points()
    spans: dict[str, dict[str, float]] = {}
    with pymupdf.open() as document:
        page = document.new_page(width=width, height=height)
        _draw_footer(page, A4, LAYOUT, left=left, counter=counter, right=right)
        for block in page.get_text("dict")["blocks"]:
            for line in block["lines"]:
                for span in line["spans"]:
                    spans[span["text"]] = {
                        "x": span["bbox"][0],
                        "baseline": span["origin"][1],
                        "color": span["color"],
                        "size": span["size"],
                    }
    return spans


def test_the_footer_segments_land_where_the_layout_puts_them() -> None:
    """The left segment starts at the margin; the right one ends at it;
    the counter is set a fixed gap clear of the right one so the two
    numbers are never mistaken for each other. The baseline sits half a
    margin up from the bottom of the cell.
    """
    import pymupdf

    from newsprint.stamp import COUNTER_GAP_PT, FONT, FONT_SIZE_PT

    counter, right = "1/2", "3 · 5 Sep 2026"
    spans = _footer_spans("Money Stuff", counter, right)

    cell_width_pt, cell_height_pt = A4.cell.as_points()
    margin_pt = LAYOUT.margin_mm * POINTS_PER_INCH / MM_PER_INCH
    right_width = pymupdf.get_text_length(right, fontname=FONT, fontsize=FONT_SIZE_PT)
    counter_width = pymupdf.get_text_length(
        counter, fontname=FONT, fontsize=FONT_SIZE_PT
    )
    expected_right_x = cell_width_pt - margin_pt - right_width
    expected_counter_x = expected_right_x - COUNTER_GAP_PT - counter_width

    assert spans["Money Stuff"]["x"] == pytest.approx(margin_pt, abs=0.01)
    assert spans[right]["x"] == pytest.approx(expected_right_x, abs=0.01)
    assert spans[counter]["x"] == pytest.approx(expected_counter_x, abs=0.01)
    for segment in ("Money Stuff", counter, right):
        assert spans[segment]["baseline"] == pytest.approx(
            cell_height_pt - margin_pt / 2, abs=0.01
        )


def test_the_footer_is_drawn_in_the_grey_the_layout_asks_for() -> None:
    """Black would compete with the body text it sits under. The colour
    reads back as one packed integer, which is what the constant becomes."""
    from newsprint.stamp import COLOR, FONT_SIZE_PT

    spans = _footer_spans("Money Stuff", "1/2", "3 · 5 Sep 2026")
    packed = sum(
        round(channel * 0xFF) << shift
        for channel, shift in zip(COLOR, (16, 8, 0), strict=True)
    )
    assert spans["Money Stuff"]["color"] == packed
    assert spans["Money Stuff"]["size"] == FONT_SIZE_PT


def test_a_line_that_needs_one_more_character_removed_loses_exactly_one() -> None:
    """Truncation walks back a character at a time and stops the moment
    the line plus its ellipsis fits. Two at a time would cut a character
    that had room, and stopping one step late would drop one that fits."""
    import pymupdf

    from newsprint.stamp import ELLIPSIS, FONT, FONT_SIZE_PT, _truncate

    def width(text: str) -> float:
        return pymupdf.get_text_length(text, fontname=FONT, fontsize=FONT_SIZE_PT)

    long_line = "Money Stuff · Matt Levine · Private Credit Gets Complicated Again"
    # Exactly enough room for "Money Stuff" and the ellipsis and no more.
    # The walk must stop on that boundary rather than one step past it.
    assert (
        _truncate(long_line, width("Money Stuff") + width(ELLIPSIS))
        == "Money Stuff" + ELLIPSIS
    )
    # And a cut that lands mid-word keeps every character that fits.
    assert _truncate(long_line, 60.0) == "Money Stuff · Matt L" + ELLIPSIS


def test_the_left_segment_is_given_the_room_up_to_the_counter() -> None:
    """The left segment runs from the margin to a gap short of the
    counter, and is cut to fit. Getting that width wrong in either
    direction either wastes the page or runs the subject into the
    numbers."""
    import pymupdf

    from newsprint.stamp import (
        COUNTER_GAP_PT,
        FONT,
        FONT_SIZE_PT,
        SEGMENT_GAP_PT,
        _truncate,
    )

    counter, right = "1/2", "3 · 5 Sep 2026"
    left = "Money Stuff · Matt Levine · Private Credit Gets Complicated Again"
    cell_width_pt, _ = A4.cell.as_points()
    margin_pt = LAYOUT.margin_mm * POINTS_PER_INCH / MM_PER_INCH
    right_x = (
        cell_width_pt
        - margin_pt
        - pymupdf.get_text_length(right, fontname=FONT, fontsize=FONT_SIZE_PT)
    )
    counter_x = (
        right_x
        - COUNTER_GAP_PT
        - pymupdf.get_text_length(counter, fontname=FONT, fontsize=FONT_SIZE_PT)
    )
    expected = _truncate(left, counter_x - SEGMENT_GAP_PT - margin_pt)

    spans = _footer_spans(left, counter, right)
    assert expected in spans, f"drew {sorted(spans)} rather than {expected!r}"
    assert expected != left, "the fixture must be long enough to be cut"


def test_a_publication_name_inside_a_longer_sender_name_is_not_repeated() -> None:
    """The Diff's mail arrives from "The Diffusion Report Team". The name
    is right there inside the sender's, so printing both would say it
    twice - and the words alone do not show it, since the sender's extra
    three outnumber the two they share."""
    assert byline("The Diff", "The Diffusion Report Team") == "The Diff"
    # The reverse containment is the ordinary Substack case, where the
    # sender is the person and the publication carries their name.
    assert byline("Casey Newton's Platformer", "Casey Newton") == (
        "Casey Newton's Platformer"
    )
