from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from shabbat_print import runlog
from shabbat_print.models import Document, Origin
from shabbat_print.picker import (
    DISPLAY_LIMIT,
    SelectionError,
    build_listing,
    parse_selection,
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
# build_listing
# ---------------------------------------------------------------------------


def test_build_listing_groups_by_publication_and_numbers_sequentially() -> None:
    candidates = [
        _doc("The Economist", "Issue A", "2026-09-03", uid=1),
        _doc("Money Stuff", "Issue B", "2026-09-02", uid=2),
        _doc("Money Stuff", "Issue C", "2026-09-04", uid=3),
    ]
    listing = build_listing(candidates)
    assert listing.total == 3
    assert len(listing.documents) == 3
    # Alphabetical group order: Money Stuff before The Economist.
    assert [document.publication for document in listing.documents] == [
        "Money Stuff",
        "Money Stuff",
        "The Economist",
    ]
    # Within a group, oldest first.
    assert [document.title for document in listing.documents] == [
        "Issue B",
        "Issue C",
        "Issue A",
    ]
    assert "Money Stuff" in listing.text
    assert "The Economist" in listing.text
    assert "1." in listing.text
    assert "2." in listing.text
    assert "3." in listing.text


def test_build_listing_caps_a_large_window_to_the_most_recent(tmp_path: Path) -> None:
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
    listing = build_listing(candidates)
    assert listing.total == count
    assert len(listing.documents) == DISPLAY_LIMIT
    # Kept the most recent DISPLAY_LIMIT, i.e. the highest-numbered issues.
    kept_titles = {document.title for document in listing.documents}
    assert f"Issue {count}" in kept_titles  # the very last (most recent)
    assert "Issue 1" not in kept_titles
    assert f"{listing.total}" in listing.text
    assert str(DISPLAY_LIMIT) in listing.text


def test_build_listing_says_how_many_when_not_capped() -> None:
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-03", uid=1)]
    listing = build_listing(candidates)
    assert "1" in listing.text


# ---------------------------------------------------------------------------
# parse_selection
# ---------------------------------------------------------------------------


def test_parse_selection_empty_input_means_skip() -> None:
    assert parse_selection("", count=10) == []
    assert parse_selection("   ", count=10) == []


def test_parse_selection_accepts_numbers_and_ranges_forgiving_of_spacing() -> None:
    assert parse_selection("3 7-9 12", count=12) == [3, 7, 8, 9, 12]


def test_parse_selection_is_forgiving_of_commas() -> None:
    assert parse_selection("3, 7-9,12", count=12) == [3, 7, 8, 9, 12]


def test_parse_selection_is_forgiving_of_a_leading_or_trailing_comma() -> None:
    """A leading/trailing comma splits to an empty token (re.split on a
    string that starts or ends with the separator) - that must be skipped
    silently rather than raising as though it were a malformed number."""
    assert parse_selection(",3,7,", count=12) == [3, 7]


def test_parse_selection_deduplicates_and_sorts() -> None:
    assert parse_selection("9 3 3 1-3", count=12) == [1, 2, 3, 9]


def test_parse_selection_accepts_a_reversed_range() -> None:
    assert parse_selection("9-7", count=12) == [7, 8, 9]


def test_parse_selection_rejects_an_out_of_range_number_by_name() -> None:
    with pytest.raises(SelectionError, match="17"):
        parse_selection("3 17", count=12)


def test_parse_selection_rejects_an_out_of_range_range_endpoint_by_name() -> None:
    with pytest.raises(SelectionError, match="15"):
        parse_selection("7-15", count=12)


def test_parse_selection_rejects_a_malformed_token_by_name() -> None:
    with pytest.raises(SelectionError, match="abc"):
        parse_selection("abc", count=12)


def test_parse_selection_rejects_zero() -> None:
    with pytest.raises(SelectionError, match="0"):
        parse_selection("0", count=12)
