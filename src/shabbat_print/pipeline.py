"""Turn cleaned documents into cell PDFs, one document at a time.

A document that fails is reported and skipped, never allowed to abort the run
and never silently dropped: it stays starred, so it comes back next week.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .clean import clean_document
from .config import Config
from .models import Document, Verdict
from .pdfutil import page_count
from .render import render
from .teaser import word_count as teaser_word_count
from .trim import fit


@dataclass(frozen=True, slots=True)
class Built:
    document: Document
    pdf: Path
    cells: int
    verdict: Verdict
    # A kept image (clean.py's own _is_argument_figure) that failed to
    # fetch or decode at render time - reported distinctly from an
    # ordinary drop, which happens earlier, in clean.py, and never
    # produces a Failure or reaches this field at all. Deduplicated:
    # trim.fit's compression retries re-render the same document, so the
    # same dead URL failing on every attempt is reported once, not once
    # per attempt.
    image_fetch_failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Failure:
    document: Document
    error: Exception


class EmptyDocumentError(Exception):
    """Cleaning left nothing to print."""


class TeaserSkippedError(Exception):
    """The cleaned body is only a headline and a link - too short to be
    worth a whole cell (H2). Raised as an ordinary build_one() failure so
    a skipped document gets the same treatment as every other failure
    path: reported, and never retired, since retirement (cli.py's `uids`)
    is derived only from what reached the PDF. Carries the measured word
    count and the threshold it fell below, so the caller can report
    exactly why without re-deriving either."""

    def __init__(self, word_count: int, min_words: int) -> None:
        self.word_count = word_count
        self.min_words = min_words
        super().__init__(f"{word_count} words, below the {min_words}-word threshold")


def build_one(
    document: Document,
    index: int,
    config: Config,
    out_dir: Path,
    render_fn: Callable[..., Path] = render,
) -> Built | Failure:
    """Build a single document, never raising: a bad newsletter becomes a
    Failure, not an exception, so a caller iterating documents one at a
    time (cli.py's progress bar, in particular) can keep going.

    `index` names this document's own subdirectory rather than sharing
    out_dir directly: render()'s output filename is derived from the
    message's identifier, and bulk mail without a Message-ID header falls
    back to the sender address in extract.py, so two such messages from
    the same sender would otherwise collide and silently overwrite one
    another under one shared out_dir.
    """
    document_dir = out_dir / f"{index:03d}"
    try:
        cleaned = clean_document(document)
        if not cleaned.html.strip():
            raise EmptyDocumentError(
                f"{document.publication}: nothing left after cleaning"
            )
        count = teaser_word_count(cleaned)
        if count < config.packet.min_words:
            raise TeaserSkippedError(count, config.packet.min_words)
        # Threaded into every render_fn call below for this one document -
        # including trim.fit's compression retries, which re-render the
        # same cleaned document and so may re-attempt the same fetch.
        # render()'s own contract is to *append* to image_fetch_failures,
        # never replace it (see render.py's docstring), so one shared list
        # across every attempt is exactly the accumulation this needs;
        # deduplication happens once, below, when building Built.
        # image_cache is the other half of the same sharing: measured
        # live against the real starred queue, a document needing a
        # compression retry was fetching the same chart URL up to 3 times
        # over before this existed - see render._fetch_and_process's own
        # docstring for the exact numbers.
        image_fetch_failures: list[str] = []
        image_cache: dict[str, bytes | None] = {}
        pdf = render_fn(
            cleaned,
            config,
            out_dir=document_dir,
            image_fetch_failures=image_fetch_failures,
            image_cache=image_cache,
        )
        fitted, verdict = fit(
            pdf,
            config.printing.paper,
            config.layout,
            # Bind cleaned/document_dir as defaults rather than relying on
            # the closure: fit() calls rerender synchronously within this
            # same call, so the late-binding B023 warning is a false
            # positive here, but binding explicitly documents that and
            # keeps the lint clean.
            rerender=lambda compression, cleaned=cleaned, document_dir=document_dir: (
                render_fn(
                    cleaned,
                    config,
                    compression=compression,
                    out_dir=document_dir,
                    image_fetch_failures=image_fetch_failures,
                    image_cache=image_cache,
                )
            ),
        )
        return Built(
            document=cleaned,
            pdf=fitted,
            cells=page_count(fitted),
            verdict=verdict,
            image_fetch_failures=tuple(dict.fromkeys(image_fetch_failures)),
        )
    except Exception as error:  # noqa: BLE001 - one bad newsletter must not stop the run
        return Failure(document=document, error=error)


def build(
    documents: Sequence[Document],
    config: Config,
    out_dir: Path,
    render_fn: Callable[..., Path] = render,
) -> tuple[list[Built], list[Failure]]:
    built: list[Built] = []
    failed: list[Failure] = []

    for index, document in enumerate(documents):
        result = build_one(document, index, config, out_dir, render_fn)
        if isinstance(result, Built):
            built.append(result)
        else:
            failed.append(result)

    return built, failed
