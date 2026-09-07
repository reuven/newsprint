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
from .trim import fit


@dataclass(frozen=True, slots=True)
class Built:
    document: Document
    pdf: Path
    cells: int
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class Failure:
    document: Document
    error: Exception


class EmptyDocumentError(Exception):
    """Cleaning left nothing to print."""


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
        pdf = render_fn(cleaned, config, out_dir=document_dir)
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
                    cleaned, config, compression=compression, out_dir=document_dir
                )
            ),
        )
        return Built(
            document=cleaned,
            pdf=fitted,
            cells=page_count(fitted),
            verdict=verdict,
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
