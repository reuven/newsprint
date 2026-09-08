from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shabbat_print import runlog
from shabbat_print.models import Document, Origin
from shabbat_print.picker import (
    DISPLAY_LIMIT,
    build_picklist,
    format_pick_date,
    layout_picklist,
    length_label,
    window_since,
)


def _doc(publication: str, title: str, day: str, uid: int = 1) -> Document:
    return Document(
        origin=Origin(kind="email", identifier=f"<{uid}@example.com>", uid=uid),
        publication=publication,
        title=title,
        date=datetime.fromisoformat(day).replace(tzinfo=UTC),
        html="<p>x</p>",
    )


# ---------------------------------------------------------------------------
# window_since
# ---------------------------------------------------------------------------


def test_window_since_falls_back_to_fallback_days_with_no_prior_run(
    tmp_path: Path,
) -> None:
    since = window_since(
        fallback_days=7, today=date(2026, 9, 7), state_dir=tmp_path / "never-created"
    )
    assert since == date(2026, 8, 31)


def test_window_since_uses_the_last_successful_runs_date(tmp_path: Path) -> None:
    runlog.record(
        {"outcome": "printed", "at": "2026-08-28T15:00:00+00:00"}, state_dir=tmp_path
    )
    since = window_since(fallback_days=7, today=date(2026, 9, 7), state_dir=tmp_path)
    assert since == date(2026, 8, 28)


def test_window_since_ignores_a_no_retire_rehearsal(tmp_path: Path) -> None:
    """printed-kept must not advance the window - a --no-retire rehearsal
    never counts as a successful run, so repeated rehearsing must not make
    the user lose track of what they have already seen."""
    runlog.record(
        {"outcome": "printed-kept", "at": "2026-09-06T15:00:00+00:00"},
        state_dir=tmp_path,
    )
    since = window_since(fallback_days=7, today=date(2026, 9, 7), state_dir=tmp_path)
    # Falls all the way back to fallback_days, as though there had been no
    # run at all - the rehearsal above must be invisible to this function.
    assert since == date(2026, 8, 31)


# ---------------------------------------------------------------------------
# length_label
# ---------------------------------------------------------------------------
#
# Measured against the user's live 99-message unstarred window (see
# .superpowers/sdd/2026-09-07-newsletter-pipeline/tui-picker.md): RFC822.SIZE
# correlates only 0.71 with real cleaned word count, and a 16-64KB
# BODY.PEEK[]<0.N> partial fetch measured no better (0.58-0.70) while
# costing 2-7.5s instead of RFC822.SIZE's 0.18s - so a size-based bucket is
# always going to be approximate, which is exactly why it is presented as a
# coarse three-way bucket rather than a specific word count or reading time.
# Thresholds are the size terciles measured on that window (~79KB / ~111KB),
# rounded for readability.


def test_length_label_short_below_the_first_threshold() -> None:
    assert length_label(20_000) == "short"


def test_length_label_medium_between_the_thresholds() -> None:
    assert length_label(90_000) == "medium"


def test_length_label_long_above_the_second_threshold() -> None:
    assert length_label(200_000) == "long"


def test_length_label_boundaries_are_inclusive_of_the_lower_bucket() -> None:
    assert length_label(80_000) == "medium"
    assert length_label(110_000) == "long"


def test_length_label_unknown_when_no_size_was_fetched() -> None:
    assert length_label(None) == "length unknown"


# ---------------------------------------------------------------------------
# build_picklist
# ---------------------------------------------------------------------------


def test_build_picklist_groups_by_publication_and_sorts_within_a_group() -> None:
    candidates = [
        _doc("The Economist", "Issue A", "2026-09-03", uid=1),
        _doc("Money Stuff", "Issue B", "2026-09-02", uid=2),
        _doc("Money Stuff", "Issue C", "2026-09-04", uid=3),
    ]
    picklist = build_picklist(candidates, sizes={})
    assert picklist.total == 3
    # Alphabetical group order: Money Stuff before The Economist.
    assert [group.publication for group in picklist.groups] == [
        "Money Stuff",
        "The Economist",
    ]
    # Within a group, oldest first.
    assert [row.document.title for row in picklist.groups[0].rows] == [
        "Issue B",
        "Issue C",
    ]


def test_build_picklist_labels_each_row_with_its_length() -> None:
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-03", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})
    assert picklist.groups[0].rows[0].length == "short"


def test_build_picklist_labels_a_row_with_no_known_size() -> None:
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-03", uid=1)]
    picklist = build_picklist(candidates, sizes={})
    assert picklist.groups[0].rows[0].length == "length unknown"


def test_build_picklist_caps_a_large_window_to_the_most_recent() -> None:
    base = date(2026, 6, 1)
    count = DISPLAY_LIMIT + 20
    candidates = [
        _doc(
            "Daily Thing",
            f"Issue {n}",
            (base + timedelta(days=n)).isoformat(),
            uid=n,
        )
        for n in range(1, count + 1)
    ]
    picklist = build_picklist(candidates, sizes={})
    assert picklist.total == count
    shown_titles = {
        row.document.title for group in picklist.groups for row in group.rows
    }
    assert len(shown_titles) == DISPLAY_LIMIT
    # Kept the most recent DISPLAY_LIMIT, i.e. the highest-numbered issues.
    assert f"Issue {count}" in shown_titles  # the very last (most recent)
    assert "Issue 1" not in shown_titles


def test_build_picklist_limit_none_shows_everything_uncapped() -> None:
    """The interactive checkbox is genuinely scrollable, so it does not
    need the text-listing's flood protection - see picker.py's own
    DISPLAY_LIMIT docstring for why the cap exists at all."""
    base = date(2026, 6, 1)
    count = DISPLAY_LIMIT + 20
    candidates = [
        _doc(
            "Daily Thing",
            f"Issue {n}",
            (base + timedelta(days=n)).isoformat(),
            uid=n,
        )
        for n in range(1, count + 1)
    ]
    picklist = build_picklist(candidates, sizes={}, limit=None)
    shown_titles = {
        row.document.title for group in picklist.groups for row in group.rows
    }
    assert len(shown_titles) == count
    assert picklist.total == count


def test_build_picklist_handles_no_candidates() -> None:
    picklist = build_picklist([], sizes={})
    assert picklist.total == 0
    assert picklist.groups == ()


# ---------------------------------------------------------------------------
# format_pick_date - the picker's own date format, deliberately without a
# year (see picker-layout.md: every candidate in the review window is from
# the same few weeks, so the year is redundant weight, not information).
# ---------------------------------------------------------------------------


def test_format_pick_date_drops_the_year() -> None:
    assert format_pick_date(date(2026, 9, 3)) == "3 Sep"


def test_format_pick_date_keeps_a_two_digit_day() -> None:
    assert format_pick_date(date(2026, 9, 13)) == "13 Sep"


def test_format_pick_date_uses_the_fixed_english_month_not_strftime() -> None:
    """Mirrors stamp.format_packet_date's own guard against a locale
    turning %b into something non-English (a Hebrew locale did exactly
    this once - see stamp.py and test_stamp.py)."""
    assert format_pick_date(date(2026, 1, 5)) == "5 Jan"


def test_build_picklist_when_has_no_year() -> None:
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-03", uid=1)]
    picklist = build_picklist(candidates, sizes={})
    assert picklist.groups[0].rows[0].when == "3 Sep"


# ---------------------------------------------------------------------------
# _display_width - terminal column width, not character count (an emoji
# can be twice as wide as a letter - see picker-layout.md point 4).
# ---------------------------------------------------------------------------


def test_display_width_counts_plain_ascii_by_character() -> None:
    from shabbat_print.picker import _display_width

    assert _display_width("Bond market Rorschach") == len("Bond market Rorschach")


def test_display_width_counts_a_wide_emoji_as_two_columns() -> None:
    from shabbat_print.picker import _display_width

    # U+1F9E0 BRAIN - a double-width emoji per wcwidth, exactly the kind
    # of subject-leading emoji the spec's example ("Claude Fable 5.1 Is
    # Here") carries.
    assert _display_width("\U0001f9e0 Title") == 2 + len(" Title")


def test_display_width_treats_a_non_printable_as_zero() -> None:
    """wcwidth returns -1 for a control character - must not be allowed
    to make a width computation negative and break every downstream
    padding/truncation sum."""
    from shabbat_print.picker import _display_width

    assert _display_width("\x1bab") == 2


# ---------------------------------------------------------------------------
# _truncate_to_width
# ---------------------------------------------------------------------------


def test_truncate_to_width_returns_text_unchanged_when_it_already_fits() -> None:
    from shabbat_print.picker import _truncate_to_width

    assert _truncate_to_width("Short", max_width=20) == "Short"


def test_truncate_to_width_snaps_back_to_the_last_full_word() -> None:
    from shabbat_print.picker import _truncate_to_width

    result = _truncate_to_width("Friction and Feedback Loops", max_width=15)
    assert result == "Friction and…"
    assert len(result) <= 15


def test_truncate_to_width_falls_back_to_a_mid_word_cut_with_no_space() -> None:
    from shabbat_print.picker import _truncate_to_width

    result = _truncate_to_width("Supercalifragilisticexpialidocious", max_width=10)
    assert result == "Supercali…"


def test_truncate_to_width_counts_emoji_as_two_columns_not_one_character() -> None:
    from shabbat_print.picker import _display_width, _truncate_to_width

    result = _truncate_to_width("\U0001f9e0 Claude Fable 5.1 Is Here", max_width=10)
    assert _display_width(result) <= 10


def test_truncate_to_width_degenerate_zero_width_returns_empty() -> None:
    from shabbat_print.picker import _truncate_to_width

    assert _truncate_to_width("Anything", max_width=0) == ""


def test_truncate_to_width_not_even_one_character_fits_beside_the_ellipsis() -> None:
    """max_width == 1 leaves no room for a real character next to the
    (width-1) ellipsis - the ellipsis alone is the whole answer."""
    from shabbat_print.picker import _ELLIPSIS, _truncate_to_width

    assert _truncate_to_width("Anything", max_width=1) == _ELLIPSIS


def test_truncate_to_width_keeps_a_mid_word_cut_when_the_only_space_is_leading() -> (
    None
):
    """Mirrors contents.py's own test of the same edge case: a leading
    space is the only space in the truncated prefix, so rsplit leaves an
    empty word_boundary - the mid-word cut is kept rather than returning
    a bare ellipsis with nothing in front of it."""
    from shabbat_print.picker import _truncate_to_width

    result = _truncate_to_width(" Extraordinarily", max_width=6)
    assert result == " Extr…"


# ---------------------------------------------------------------------------
# _row_text - a dirty subject must never break the terminal's own
# rendering. Found on a real, live queue (a message titled "Static vs.
# Dynamic vs. Continuous Batching in LLMs, clearly\r\nexplained!" - an
# embedded CRLF, presumably an unfolded header continuation): the raw
# carriage return is a literal terminal control character, so questionary
# handing it straight to the terminal jumps the cursor back to column 0
# mid-row and wraps the line - the exact "wrapping into a mess" the
# picker-layout spec's narrow-terminal note warns against, except here
# triggered by dirty data rather than a narrow terminal. No amount of
# column-width math prevents that; the control character itself has to be
# neutralised before layout.
# ---------------------------------------------------------------------------


def test_row_text_strips_an_embedded_carriage_return() -> None:
    from shabbat_print.picker import _row_text

    text = _row_text(
        "Static vs. Dynamic vs. Continuous Batching in LLMs, clearly\r\nexplained!",
        "1 Sep",
        "long",
        subject_width=98,
        date_width=5,
    )
    assert "\r" not in text
    assert "\n" not in text
    assert "clearly explained!" in text


def test_layout_picklist_never_emits_a_control_character_in_a_row() -> None:
    candidates = [
        _doc("Daily Dose of DS", "Clearly\r\nexplained!", "2026-09-01", uid=1),
    ]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=120)
    row = next(line for line in lines if line.kind == "row")
    assert all(ch >= " " for ch in row.text)


# ---------------------------------------------------------------------------
# _heading_rule - the ruled, capitalised group heading.
# ---------------------------------------------------------------------------


def test_heading_rule_fills_to_the_given_width() -> None:
    from shabbat_print.picker import _display_width, _heading_rule

    rule = _heading_rule("Axios Macro", 46)
    assert _display_width(rule) == 46
    label = "── AXIOS MACRO "
    assert rule == label + "─" * (46 - len(label))


def test_heading_rule_uppercases_the_publication() -> None:
    from shabbat_print.picker import _heading_rule

    assert "BEN THOMPSON" in _heading_rule("Ben Thompson", 40)


def test_heading_rule_truncates_a_publication_longer_than_the_width() -> None:
    from shabbat_print.picker import _display_width, _heading_rule

    rule = _heading_rule("An Extremely Long Publication Name That Never Fits", 20)
    assert _display_width(rule) <= 20


# ---------------------------------------------------------------------------
# _column_widths
# ---------------------------------------------------------------------------


def test_column_widths_size_date_and_length_from_the_actual_data() -> None:
    from shabbat_print.picker import _column_widths

    rows = [
        PickRowStub(when="1 Sep", length="short"),
        PickRowStub(when="13 Sep", length="medium"),
    ]
    subject_width, date_width, length_width = _column_widths(rows, width=80)
    assert date_width == len("13 Sep")
    assert length_width == len("[medium]")
    assert subject_width == 80 - 5 - date_width - length_width - 4


def test_column_widths_floors_the_subject_width_in_a_narrow_terminal() -> None:
    from shabbat_print.picker import _MIN_SUBJECT_WIDTH, _column_widths

    rows = [PickRowStub(when="13 Sep", length="length unknown")]
    subject_width, _date_width, _length_width = _column_widths(rows, width=40)
    assert subject_width == _MIN_SUBJECT_WIDTH


def test_column_widths_has_sane_defaults_with_no_rows() -> None:
    from shabbat_print.picker import _column_widths

    subject_width, date_width, length_width = _column_widths([], width=80)
    assert date_width > 0
    assert length_width > 0
    assert subject_width > 0


class PickRowStub:
    """A minimal stand-in carrying only what _column_widths reads
    (`when`, `length`) - a real PickRow also requires a Document, which
    these column-sizing tests have no need to construct."""

    def __init__(self, when: str, length: str) -> None:
        self.when = when
        self.length = length


# ---------------------------------------------------------------------------
# layout_picklist - the whole rendered picker list, as plain PickLine data.
# ---------------------------------------------------------------------------


def test_layout_picklist_puts_a_blank_line_before_every_heading() -> None:
    candidates = [
        _doc("Axios Macro", "G20 growth divide", "2026-09-01", uid=1),
        _doc("Axios Macro", "Bond market Rorschach", "2026-09-02", uid=2),
        _doc("Ben Thompson", "Friction and Feedback", "2026-09-04", uid=3),
    ]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=80)
    kinds = [line.kind for line in lines]
    # blank, heading, row, row, blank, heading, row
    assert kinds == ["blank", "heading", "row", "row", "blank", "heading", "row"]


def test_layout_picklist_heading_text_is_ruled_and_uppercase() -> None:
    candidates = [_doc("Axios Macro", "G20 growth divide", "2026-09-01", uid=1)]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=80)
    heading = next(line for line in lines if line.kind == "heading")
    assert heading.text.startswith("── AXIOS MACRO ")
    assert heading.text.endswith("─")


def test_layout_picklist_row_carries_its_document() -> None:
    candidates = [_doc("Axios Macro", "G20 growth divide", "2026-09-01", uid=1)]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=80)
    row = next(line for line in lines if line.kind == "row")
    assert row.document is picklist.groups[0].rows[0].document


def test_layout_picklist_blank_and_heading_lines_carry_no_document() -> None:
    candidates = [_doc("Axios Macro", "G20 growth divide", "2026-09-01", uid=1)]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=80)
    for line in lines:
        if line.kind != "row":
            assert line.document is None


def test_layout_picklist_aligns_the_date_column_across_different_groups() -> None:
    """The whole point of the fix: the date and length columns must form
    scannable columns down the WHOLE list, not just within one group -
    see picker-layout.md point 3."""
    candidates = [
        _doc("Axios Macro", "G20 growth divide", "2026-09-01", uid=1),
        _doc("Ben Thompson", "Friction and Feedback (This Week…)", "2026-09-04", uid=2),
    ]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=80)
    rows = [line for line in lines if line.kind == "row"]
    assert len(rows) == 2
    # Both rows' "1 Sep"/"4 Sep" date field must start at the same
    # character offset - that offset is what makes the list scannable.
    offsets = {
        row.text.index(row_when)
        for row, row_when in zip(rows, ["1 Sep", "4 Sep"], strict=True)
    }
    assert len(offsets) == 1


def test_layout_picklist_leading_emoji_does_not_shift_the_date_column() -> None:
    """A subject starting with an emoji must line up with one that
    doesn't - see picker-layout.md point 4.

    The comparison must be in terminal display columns, not Python
    string index: a leading emoji is one Python character but two
    display columns, so the raw string index of the date field on the
    emoji row is genuinely one character *less* than on the plain row
    (padding equalises the on-screen column, not the character count) -
    caught by hand-checking a first draft of this test that compared
    str.index() directly and failed on a real, correctly-aligned render.
    """
    from shabbat_print.picker import _display_width

    candidates = [
        _doc("Axios Macro", "\U0001f9e0 Claude Fable 5.1 Is Here", "2026-09-01", uid=1),
        _doc("Axios Macro", "G20 growth divide", "2026-09-02", uid=2),
    ]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=80)
    rows = [line for line in lines if line.kind == "row"]
    assert len(rows) == 2
    columns = {
        _display_width(row.text[: row.text.index(row_when)])
        for row, row_when in zip(rows, ["1 Sep", "2 Sep"], strict=True)
    }
    assert len(columns) == 1


def test_layout_picklist_truncates_a_long_subject_with_an_ellipsis() -> None:
    candidates = [
        _doc(
            "Stratechery",
            "Write Things Down (Stratechery Archives, revisited at length)",
            "2026-09-08",
            uid=1,
        )
    ]
    picklist = build_picklist(candidates, sizes={})
    lines = layout_picklist(picklist, width=50)
    row = next(line for line in lines if line.kind == "row")
    assert "…" in row.text


def test_layout_picklist_degrades_sensibly_in_a_narrow_terminal() -> None:
    """Below _MIN_TERMINAL_WIDTH, the floor takes over rather than
    crushing every column toward zero - a 10-column terminal renders the
    same as the floor width, not an unreadable sliver."""
    candidates = [
        _doc("Axios Macro", "G20 growth divide", "2026-09-01", uid=1),
    ]
    picklist = build_picklist(candidates, sizes={})
    narrow = layout_picklist(picklist, width=10)
    floored = layout_picklist(picklist, width=40)
    narrow_row = next(line for line in narrow if line.kind == "row")
    floored_row = next(line for line in floored if line.kind == "row")
    assert narrow_row.text == floored_row.text


def test_layout_picklist_handles_no_candidates() -> None:
    picklist = build_picklist([], sizes={})
    assert layout_picklist(picklist, width=80) == ()
