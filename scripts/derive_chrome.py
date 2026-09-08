"""Derive newsletter-chrome rules from real evidence: what repeats.

Three previous rounds of chrome-detection rules each started from a string
the user happened to notice on a printed page, so the rule set could only
ever be as complete as one person's patience for reading. This script
inverts that: it runs `extract()` and `clean_document()` over every message
in the user's local "toprint" Thunderbird archive, collects every line that
*survives* cleaning, and reports each one ranked by how many distinct
publications and messages it appears in. Chrome repeats across
publications - the same "Unsubscribe" or "Privacy Policy" line shows up in
newsletter after newsletter, from senders who share no editorial content at
all - while article prose does not, so a line clearing both a message
threshold and a publication threshold is strong, direct evidence of chrome
this project has not yet named, rather than a guess about what a template
might contain.

This is an instrument, not a rule-writer: it reports, it does not modify
clean.py or delete anything. A human (see the derive-chrome spec, part D)
still reads the output and judges each candidate - a general pattern where
one explains a family of hits, a literal only where no safe generalisation
exists - and every derived rule still needs the same structural safety
argument every other rule in clean.py needs (see clean.py's own module
docstring): frequency is evidence a line is *common*, not proof it is safe
to delete wherever it appears.

Line splitting reuses clean.py's own _rendered_lines: a rendered line is
split only at a genuine block boundary (an explicit <br>, or a non-inline
tag), never at an inline tag's own start or end. Before the derive-chrome
spec's part B fixed this, splitting instead happened at every bs4 text
fragment - including ones separated only by an inline <em>/<a>/<strong> -
so a real sentence like "...in France, and later in Spain" surfaced here as
several short, meaningless "lines" (", and", "here", "from") that cleared
both thresholds purely as a markup artifact. Reusing the fixed splitter,
rather than re-fragmenting independently, is what keeps this tool's own
output from being polluted by the same bug a second time.

Read-only: it only reads the local mbox file `find_thunderbird_mbox()`
finds. It never touches IMAP, the keychain, CUPS, or Preview, and writes
nothing but stdout.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from shabbat_print.clean import _rendered_lines, clean_document
from shabbat_print.extract import extract
from shabbat_print.mbox import find_thunderbird_mbox, split_mbox

# Part A's own measurement ("96 distinct lines clear >=8 messages and >=4
# publications in that sample alone") is the default here, so a plain
# `derive_chrome.py` run reproduces it.
_DEFAULT_MIN_MESSAGES = 8
_DEFAULT_MIN_PUBLICATIONS = 4


def _surviving_lines(html: str) -> set[str]:
    """The distinct rendered lines of a cleaned document's HTML.

    Uses clean.py's own _rendered_lines (see this module's docstring) so
    this tool's frequency counts are not polluted by the same inline-tag
    fragmentation bug part B of the derive-chrome spec fixed. A set, not a
    list: a line repeated several times within one message (a nav row
    appearing at both top and bottom, say) must count as ONE message for
    that line, not several - this script is measuring how many distinct
    *messages* a line appears in, the same way Part A's own sample does.
    """
    soup = BeautifulSoup(html, "lxml")
    root: Tag = soup.body if soup.body is not None else soup
    return {line for line in _rendered_lines(root) if line.strip()}


def _iter_raw_messages(mbox: Path, limit: int | None) -> Iterator[bytes]:
    messages = split_mbox(mbox)
    if limit is not None:
        messages = itertools.islice(messages, limit)
    yield from messages


def _process(
    raw: bytes,
    messages_by_line: dict[str, int],
    publications_by_line: dict[str, set[str]],
) -> tuple[bool, str | None]:
    """Returns (processed_ok, error_message)."""
    try:
        document = extract(raw)
        cleaned = clean_document(document)
    except Exception as error:  # noqa: BLE001 - one bad message must not stop the run
        return False, f"{type(error).__name__}: {error}"
    for line in _surviving_lines(cleaned.html):
        messages_by_line[line] += 1
        publications_by_line[line].add(document.publication)
    return True, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process only the first N messages, for a quick run",
    )
    parser.add_argument(
        "--mbox",
        type=Path,
        default=None,
        help="path to a Thunderbird mbox file (default: auto-detected)",
    )
    parser.add_argument(
        "--min-messages",
        type=int,
        default=_DEFAULT_MIN_MESSAGES,
        help=f"only report lines appearing in at least this many distinct "
        f"messages (default: {_DEFAULT_MIN_MESSAGES})",
    )
    parser.add_argument(
        "--min-publications",
        type=int,
        default=_DEFAULT_MIN_PUBLICATIONS,
        help="only report lines appearing in at least this many distinct "
        f"publications (default: {_DEFAULT_MIN_PUBLICATIONS})",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="show only the top N lines after both thresholds are applied "
        "(default: show all of them)",
    )
    args = parser.parse_args(argv)

    mbox = args.mbox if args.mbox is not None else find_thunderbird_mbox()
    if mbox is None or not mbox.exists():
        print("no local Thunderbird mbox found; pass --mbox PATH", file=sys.stderr)
        return 1

    messages_by_line: dict[str, int] = defaultdict(int)
    publications_by_line: dict[str, set[str]] = defaultdict(set)
    processed = 0
    errors = 0

    started = time.perf_counter()
    for count, raw in enumerate(_iter_raw_messages(mbox, args.limit), start=1):
        ok, _error = _process(raw, messages_by_line, publications_by_line)
        if ok:
            processed += 1
        else:
            errors += 1
        if count % 200 == 0:
            print(f"...{count} messages processed", file=sys.stderr)
    elapsed = time.perf_counter() - started

    rows = [
        (len(publications_by_line[line]), messages_by_line[line], line)
        for line in messages_by_line
        if messages_by_line[line] >= args.min_messages
        and len(publications_by_line[line]) >= args.min_publications
    ]
    rows.sort(key=lambda row: (-row[0], -row[1], row[2]))
    if args.top is not None:
        rows = rows[: args.top]

    print("shabbat-print chrome derivation")
    print("================================")
    if args.limit is not None:
        print(f"(limited to the first {args.limit} messages)")
    print(f"messages processed: {processed}")
    if errors:
        print(f"messages that failed to process: {errors}")
    print(f"distinct surviving lines seen: {len(messages_by_line)}")
    print(
        f"lines clearing >={args.min_messages} messages and "
        f">={args.min_publications} publications: {len(rows)}"
    )
    print(f"run time: {elapsed:.1f}s")
    print()

    for pubs, msgs, line in rows:
        print(f"{pubs:3d} pubs {msgs:4d} msgs  {line!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
