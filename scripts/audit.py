"""Measure clean.py's chrome-removal heuristics against the user's own mail.

This is an instrument, not a test: it reports, it does not pass or fail.
clean.py's heuristics (a boilerplate phrase list, postal-address
recognition, whole-line exact matches, delimiter-separated nav rows,
leading/trailing-run removal, invisible-character pruning) were each
validated by hand against the fixture corpus when they were written. They
will drift as publications change their templates - the fixture corpus
already shows one publication rewriting its markup completely between
2024 and 2026 - and that drift is invisible until someone reads the
output by hand again. This script makes that reading repeatable: it runs
`extract` and `clean_document` over every message in the user's local
"toprint" archive and prints what was removed, so a human can judge
whether the heuristics are still behaving.

Read-only: it only reads the local mbox file `find_thunderbird_mbox()`
finds. It never touches IMAP, the keychain, CUPS, or Preview, and writes
nothing but stdout.

Rule attribution
-----------------
Document.blocks_dropped is a flat tuple - clean_document() does not record
which of its five removal passes produced each entry. Rather than adding
that bookkeeping to clean.py itself (a data-model change this script does
not need anywhere else), `_attribute_by_rule` below monkeypatches the five
pass functions for the duration of a single clean_document() call, purely
to record which DroppedBlock objects each one returns. Attribution is
then done by object identity, not by assuming an order or a count:
DroppedBlock instances flow through `dataclasses.replace()` unchanged
(never copied), so the exact objects a pass returns are the exact objects
that end up in the final Document.blocks_dropped. Anything in
blocks_dropped that cannot be traced to one of the five wrapped passes -
which would only happen if clean.py grows a new removal pass this script
does not yet know about - is bucketed as "ungrouped" instead of being
silently mis-attributed.
"""

from __future__ import annotations

import argparse
import email
import itertools
import re
import sys
import textwrap
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from make_fixtures import platform_of

from newsprint import clean as clean_module
from newsprint.clean import _INVISIBLE_CHARS, clean_document
from newsprint.config import DEFAULTS
from newsprint.extract import extract
from newsprint.mbox import find_thunderbird_mbox, split_mbox
from newsprint.models import DroppedBlock
from newsprint.teaser import word_count as teaser_word_count

# The taxonomy this report groups by. Anything platform_of() recognizes
# outside this set (e.g. mailgun, klaviyo - real platforms, just not ones
# the brief asked this histogram to break out) folds into "other", so the
# histogram's shape stays fixed even as the underlying detector grows.
_PLATFORMS = (
    "substack",
    "beehiiv",
    "mailchimp",
    "sendgrid",
    "convertkit",
    "ghost",
    "buttondown",
    "customer.io",
    "sailthru",
    "amazonses",
    "iterable",
    "other",
)

# The five passes clean_document() runs, in the order it runs them, paired
# with the module-level attribute name this script patches to observe
# each one's return value. See the module docstring for why identity,
# not this order, is what the final attribution relies on.
_RULES: tuple[tuple[str, str], ...] = (
    ("line_chrome", "_strip_line_chrome"),
    ("leading_chrome_run", "_strip_leading_chrome_run"),
    ("duplicate_title_block", "_strip_duplicate_title_block"),
    ("trailing_chrome_run", "_strip_trailing_chrome_run"),
    ("chrome_blocks", "_strip_chrome_blocks"),
)

# Outliers: a message losing more than this share of its words is worth a
# human's attention first (see the module docstring and the brief this
# script was built from).
_OUTLIER_THRESHOLD_PCT = 5.0

_MIN_WORDS = DEFAULTS["packet"]["min_words"]

_TEXT_WIDTH = 160

# clean.py's own preheader-padding characters (see _prune_invisible_elements
# there). A raw preheader run interleaves ordinary spaces with dozens of
# these between two rendered "words", and str.split() treats each isolated
# run of them as its own token - so an uncorrected word count sees a single
# invisible padding block as hundreds of "words". That padding is real
# (measured directly against a live fixture: one short message's naive
# before-count of 261 dropped to 62 once these were collapsed first, with
# no change to what a human would call a word), and clean.py already
# removes it correctly; this just keeps this script's own counting from
# mistaking it for content in the first place. Reusing clean.py's own
# character set, rather than redefining it here, keeps the two definitions
# of "invisible" from silently drifting apart.
_INVISIBLE_RUN = re.compile(f"[{re.escape(_INVISIBLE_CHARS)}]+")


@contextmanager
def _attribute_by_rule() -> Iterator[dict[str, list[DroppedBlock]]]:
    """See the module docstring's "Rule attribution" section."""
    captured: dict[str, list[DroppedBlock]] = {rule: [] for rule, _ in _RULES}
    originals = {attr: getattr(clean_module, attr) for _, attr in _RULES}

    def _wrap(rule: str, original: object) -> object:
        def wrapper(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)  # type: ignore[operator]
            captured[rule].extend(result)  # type: ignore[arg-type]
            return result

        return wrapper

    for rule, attr in _RULES:
        setattr(clean_module, attr, _wrap(rule, originals[attr]))
    try:
        yield captured
    finally:
        for attr, original in originals.items():
            setattr(clean_module, attr, original)


def _word_count(html: str) -> int:
    """A plain word count, used for the before/after totals and the
    outlier percentage. Deliberately not teaser.word_count(): that
    function excludes any line matching the Subject, which is the right
    rule for deciding whether a *cleaned* document is a teaser, but would
    make the "before cleaning" side of this comparison inconsistent with
    itself for no reason - here we just want how much text left the
    document.
    """
    text = _INVISIBLE_RUN.sub(" ", BeautifulSoup(html, "lxml").get_text())
    return len(text.split())


@dataclass(slots=True)
class Stats:
    processed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    words_before: int = 0
    words_after: int = 0
    images_kept: int = 0
    images_dropped: int = 0
    platform_counts: Counter[str] = field(default_factory=Counter)
    dropped_by_rule: dict[str, list[tuple[str, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    outliers: list[tuple[str, str, int, int, float]] = field(default_factory=list)
    teaser_skips: list[tuple[str, str, int]] = field(default_factory=list)


def _platform_of(raw: bytes) -> str:
    detected = platform_of(email.message_from_bytes(raw))
    return detected if detected in _PLATFORMS else "other"


def _process(raw: bytes, stats: Stats) -> None:
    try:
        document = extract(raw)
    except Exception as error:  # noqa: BLE001 - one bad message must not stop the audit
        stats.errors.append(("(unparsed message)", f"{type(error).__name__}: {error}"))
        return

    label = f"{document.publication}: {document.title!r}"
    try:
        words_before = _word_count(document.html)
        with _attribute_by_rule() as captured:
            cleaned = clean_document(document)
        words_after = _word_count(cleaned.html)
    except Exception as error:  # noqa: BLE001 - see above
        stats.errors.append((label, f"{type(error).__name__}: {error}"))
        return

    stats.processed += 1
    stats.words_before += words_before
    stats.words_after += words_after
    stats.images_kept += cleaned.images_kept
    stats.images_dropped += len(cleaned.images_dropped)
    stats.platform_counts[_platform_of(raw)] += 1

    captured_ids = {id(block) for blocks in captured.values() for block in blocks}
    for rule, blocks in captured.items():
        stats.dropped_by_rule[rule].extend(
            (document.publication, block.text) for block in blocks
        )
    ungrouped = [
        block for block in cleaned.blocks_dropped if id(block) not in captured_ids
    ]
    stats.dropped_by_rule["ungrouped"].extend(
        (document.publication, block.text) for block in ungrouped
    )

    if words_before > 0:
        pct_lost = (words_before - words_after) / words_before * 100
        if pct_lost > _OUTLIER_THRESHOLD_PCT:
            stats.outliers.append(
                (
                    document.publication,
                    document.title,
                    words_before,
                    words_after,
                    pct_lost,
                )
            )

    count = teaser_word_count(cleaned)
    if count < _MIN_WORDS:
        stats.teaser_skips.append((document.publication, document.title, count))


def _print_report(stats: Stats, elapsed_seconds: float, limit: int | None) -> None:
    print("newsprint newsletter audit")
    print("=" * 31)
    if limit is not None:
        print(f"(limited to the first {limit} messages)")
    print()

    print("Totals")
    print("------")
    print(f"messages processed: {stats.processed}")
    if stats.errors:
        print(f"messages that failed to process: {len(stats.errors)}")
    print(f"words before cleaning: {stats.words_before}")
    print(f"words after cleaning:  {stats.words_after}")
    removed = stats.words_before - stats.words_after
    pct = (removed / stats.words_before * 100) if stats.words_before else 0.0
    print(f"words removed: {removed} ({pct:.2f}%)")
    print(f"images kept: {stats.images_kept}, images dropped: {stats.images_dropped}")
    print(f"run time: {elapsed_seconds:.1f}s")
    print()

    print("Platform histogram")
    print("-------------------")
    width = max(len(name) for name in _PLATFORMS)
    for platform in sorted(_PLATFORMS, key=lambda p: (-stats.platform_counts[p], p)):
        print(f"  {stats.platform_counts[platform]:5d}  {platform.ljust(width)}")
    print()

    print(f"Outliers (>{_OUTLIER_THRESHOLD_PCT:.0f}% of words lost)")
    print("-------------------------")
    if not stats.outliers:
        print("  (none)")
    else:
        for publication, subject, before, after, outlier_pct in sorted(
            stats.outliers, key=lambda item: -item[4]
        ):
            subject_preview = textwrap.shorten(subject, width=80, placeholder="...")
            print(
                f"  {outlier_pct:5.1f}%  {publication}: {subject_preview!r} "
                f"({before} -> {after} words)"
            )
    print()

    print(f"Teaser skips (below {_MIN_WORDS} words)")
    print("--------------------------")
    if not stats.teaser_skips:
        print("  (none)")
    else:
        for publication, subject, count in sorted(
            stats.teaser_skips, key=lambda item: item[2]
        ):
            subject_preview = textwrap.shorten(subject, width=80, placeholder="...")
            print(f"  {count:5d} words  {publication}: {subject_preview!r}")
    print()

    total_dropped = sum(len(items) for items in stats.dropped_by_rule.values())
    print(f"Removed blocks/lines ({total_dropped} total)")
    print("=================================")
    for rule in (*[name for name, _ in _RULES], "ungrouped"):
        items = stats.dropped_by_rule.get(rule, [])
        print()
        print(f"-- {rule} ({len(items)}) --")
        for publication, text in items:
            preview = textwrap.shorten(
                text.replace("\n", " "), width=_TEXT_WIDTH, placeholder="..."
            )
            print(f"  [{publication}] {preview}")

    if stats.errors:
        print()
        print(f"Processing errors ({len(stats.errors)})")
        print("=========================")
        for label, message in stats.errors:
            print(f"  {label}: {message}")


def _iter_raw_messages(mbox: Path, limit: int | None) -> Iterator[bytes]:
    messages = split_mbox(mbox)
    if limit is not None:
        messages = itertools.islice(messages, limit)
    yield from messages


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
    args = parser.parse_args(argv)

    mbox = args.mbox if args.mbox is not None else find_thunderbird_mbox()
    if mbox is None or not mbox.exists():
        print("no local Thunderbird mbox found; pass --mbox PATH", file=sys.stderr)
        return 1

    stats = Stats()
    started = time.perf_counter()
    for count, raw in enumerate(_iter_raw_messages(mbox, args.limit), start=1):
        _process(raw, stats)
        if count % 200 == 0:
            print(f"...{count} messages processed", file=sys.stderr)
    elapsed = time.perf_counter() - started

    _print_report(stats, elapsed, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
