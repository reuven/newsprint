"""Choose additional newsletters from the week's unstarred mail.

The starred queue is always the default packet; this is the "too" in the
user's original request - "I sometimes want to print some of the week's
non-starred newsletters, too." Nothing here cleans or renders a candidate:
that only happens for whatever actually gets picked, in
pipeline.build_one(), exactly as it does for the starred queue - most of
what is shown here will never be picked, so doing that work up front would
make a large week slow for no reason.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .models import Document
from .runlog import last_successful_run
from .stamp import format_packet_date

# The user's own archive holds roughly 2,200 messages, of which a week is
# roughly 25 - but the window can grow far larger than that if a while has
# passed since the last successful run. Past this many candidates, the
# terminal listing would flood rather than inform.
DISPLAY_LIMIT = 60

_TOKEN_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


class SelectionError(Exception):
    """The typed selection named something that is not on the list."""


def window_since(
    fallback_days: int, today: date, state_dir: Path | None = None
) -> date:
    """The first day the unstarred review window should include.

    `last_successful_run` already distinguishes `printed` (a genuine run)
    from `printed-kept` (a --no-retire rehearsal, which deliberately does
    not count) - see runlog.last_successful_run's own docstring. That
    means a rehearsal never advances this window, so the user does not
    lose track of what they have already been shown.
    """
    last = last_successful_run(state_dir=state_dir)
    if last is not None:
        return last.date()
    return today - timedelta(days=fallback_days)


@dataclass(frozen=True, slots=True)
class Listing:
    """The candidates as numbered rows, ready to print.

    `documents[i]` is row `i + 1` in `text` - the mapping a typed
    selection is resolved against.
    """

    documents: tuple[Document, ...]
    total: int
    text: str


def build_listing(
    candidates: Sequence[Document], limit: int = DISPLAY_LIMIT
) -> Listing:
    """Group by publication, number the rows, and cap a large window to
    the most recent `limit` - saying how many there really were either
    way, so the count on screen is never silently partial without saying
    so."""
    total = len(candidates)
    shown = list(candidates)
    if total > limit:
        shown = sorted(shown, key=lambda document: document.date, reverse=True)[:limit]

    groups: dict[str, list[Document]] = {}
    for document in shown:
        groups.setdefault(document.publication, []).append(document)
    for publication_documents in groups.values():
        publication_documents.sort(key=lambda document: document.date)

    lines: list[str] = []
    if total > limit:
        lines.append(
            f"  {total} unstarred newsletters found; showing the most recent {limit}."
        )
    else:
        lines.append(f"  {total} unstarred newsletter(s) found:")
    lines.append("")

    ordered: list[Document] = []
    row = 0
    for publication in sorted(groups, key=str.casefold):
        lines.append(f"  {publication}")
        for document in groups[publication]:
            row += 1
            ordered.append(document)
            when = format_packet_date(document.date.date())
            lines.append(f"    {row:>3}. {document.title}  ({when})")
        lines.append("")

    return Listing(
        documents=tuple(ordered), total=total, text="\n".join(lines).rstrip("\n")
    )


def parse_selection(text: str, count: int) -> list[int]:
    """Turn '3 7-9 12' into [3, 7, 8, 9, 12].

    Forgiving of extra spaces and commas between tokens. Empty (or
    whitespace-only) input means skip: returns []. A number outside
    [1, count], in either a bare number or a range endpoint, raises
    SelectionError naming that number specifically - never silently
    ignored - as does a token that is not a number or a range at all.
    """
    stripped = text.strip()
    if not stripped:
        return []

    picked: set[int] = set()
    for token in re.split(r"[\s,]+", stripped):
        if not token:
            continue
        match = _TOKEN_RE.match(token)
        if not match:
            raise SelectionError(f"{token!r} is not a number or a range")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start > end:
            start, end = end, start
        for endpoint in (start, end):
            if not (1 <= endpoint <= count):
                raise SelectionError(
                    f"no newsletter numbered {endpoint} (there are {count})"
                )
        picked.update(range(start, end + 1))

    return sorted(picked)
