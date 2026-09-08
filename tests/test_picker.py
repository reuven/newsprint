from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shabbat_print import runlog
from shabbat_print.models import Document, Origin
from shabbat_print.picker import (
    DISPLAY_LIMIT,
    build_picklist,
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
